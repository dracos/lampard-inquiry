#from datetime import datetime
import glob
import json
import os
import re
import string
from collections import Counter

ACRONYMS = {
}

class Section(object):
    def __init__(self, heading, level=1):
        self.heading = heading
        self.level = level

class Speech(object):
    def __init__(self, speaker, text, speaker_display=None, typ=None):
        self.speaker = speaker
        self.speaker_display = speaker_display
        self.text = [[text]]
        self.type = typ

    def add_para(self, text):
        self.text.append([text])

    def add_text(self, text):
        self.text[-1].append(text)

META = {
    'evidence': {},
    'urls': {},
    'videos': {},
}

def load_data():
    meta = json.load(open('data/metadata.json'))
    META['urls'].update(meta['urls'])
    META['videos'].update(meta['videos'])

def header(date):
    videos = META['videos'].get(date, [])
    if not videos:
        return ''

    out = f'''.. raw:: html

   <details id="hearing-meta" open>
        <summary>
            <span class="open">Hide video</span>
            <span class="closed">Show video</span>
        </summary>
'''
    seen = set()
    for v in videos:
        if v['id'] in seen:
            continue
        out += f'   <lite-youtube videoid="{v["id"]}" params="rel=0"></lite-youtube>\n'
        seen.add(v['id'])
    out += '   </details>\n\n'
    return out

def parse_speech(speech):
    text = '\n\n'.join([' '.join(s) for s in speech.text])
    text = text.strip()
    if not text:
        return ''

    for e in META['evidence'].keys():
        text = text.replace(e, f'`{e} <{META["evidence"][e]}>`_')

    # Deal with some acronyms
    for acronym, meaning in ACRONYMS.items():
        text = re.sub(fr'\b{acronym}\b', f':abbr:`{acronym} ({meaning})`', text, count=1)

    if speech.speaker:
        out = f"**{speech.speaker}**: {text}"
    else:
        out = f"*{text}*"

    if speech.type == 'answer':
        out = re.sub('\n\n', '\n\n.. rst-class:: indented\n\n', out)
        out = f".. rst-class:: indented\n\n{out}"

    return f"{out}\n\n"

def parse_transcripts():
    for f in sorted(glob.glob('data/*.scraped.txt')):
        if 'raw.txt' in f: continue # Ignore PDF processed
        date, title, sect = re.match('data/(\d\d\d\d-\d\d-\d\d)-(Hearing Day .*?) (?:– )?(.*).scraped.txt$', f).groups()
        sect = sect.lower().replace(' ', '-')
        if sect == 'opening-statements-and-commemorative-and-impact-accounts':
            sect = 'commemorative-and-impact-accounts'
        os.makedirs(sect, exist_ok=True)
        outfile = f'{sect}/{date}'

        with open(f, 'r', encoding='utf-8') as fp:
            if os.path.exists(f'{outfile}.rst'):
                print(f"Reparsing {f}")
            else:
                print(f"\033[31mPARSING {f}\033[0m")
            with open(f'{outfile}.rst', 'w') as out:
                url = META['urls'][date]
                out.write(f'.. raw:: html\n\n   <a id="hearing-link" href="{url}">Official hearing page</a>\n\n')
                speeches = list(parse_transcript(f, fp))

                level1_heading = []
                for speech in speeches:
                    if isinstance(speech, Section) and speech.level == 1:
                        words = [s for s in speech.heading.split(' ') if s not in ('Mr', 'Mrs', 'Ms', 'Dr', 'Sir', 'Lord', 'Baroness')]
                        if (len(words) == 2 or (len(words) == 4 and words[2] == 'of')) and 'Housekeeping' not in words:
                            level1_heading.append(' '.join(words))
                if level1_heading:
                    if len(level1_heading) > 2:
                        level1_heading = ', '.join(level1_heading[:-2])  + ', ' + ' and '.join(level1_heading[-2:])
                    elif len(level1_heading) == 2:
                        level1_heading = ' and '.join(level1_heading)
                    else:
                        level1_heading = level1_heading[0]
                    title += ' – ' + level1_heading

                out.write(title + '\n' + '=' * len(title) + '\n\n')
                out.write(header(date))
                for speech in speeches:
                    if isinstance(speech, Speech):
                        out.write(parse_speech(speech))
                    elif isinstance(speech, Section):
                        if speech.level == 1:
                            out.write(speech.heading + '\n' + '-' * len(speech.heading) + '\n\n')
                        elif speech.level == 2:
                            out.write(speech.heading + '\n' + '^' * len(speech.heading) + '\n\n')

def strip_line_numbers(url, text):
    page, num = 1, 1
    state = 'text'
    data = {}
    for line in text:
        # print("L", line)
        line = line.rstrip('\n')

        # Page break
        if '\014' in line:
            page += 1
            num = 1
            line = line.replace('\014', '')

        # Empty line
        if re.match('\s*$', line):
            continue

        # Start of index, ignore from then on
        if re.match(' *\d* +I ?N ?D ?E ?X$', line) or '...............' in line:
            state = 'index'
            continue
        if state == 'index':
            continue

        # Special fixes (day 5)
        if line in ('involved in this', 'also an', 'was', 'twin', 'Walton,', 'see', 'countryside, green', '                   achievements.'):
            data[page][-1][1] += ' ' + line
            continue

        # Just after last line, there should be a page number
        if num == 26 and not re.match('26 +I appear on behalf of the Mid and South Essex', line):
            m = re.match(' +(\d+)$', line)
            assert int(m.group(1)) == page
            continue
        elif num == 27 \
                or (num == 23 and page == 94 and '2024-09-24' in url) \
                or (num == 3 and page == 95 and '2024-09-24' in url):
            m = re.match(' +(\d+)$', line)
            assert int(m.group(1)) == page
            continue

        # Let's check we haven't lost a line anywhere...
        assert re.match(' *%d( |$)' % num, line), '%s != %s' % (num, line)

        # Strip the line number
        line = re.sub('^ *%d' % num, '', line)

        # Okay, here we have a non-page number, non-index line of just text
        data.setdefault(page, []).append([num, line])
        num += 1

    return data

def remove_left_indent(data, indent_totals):
    # Work out how indented everything is
    for page in data.keys():
        # min_indent = 999
        totals = Counter()
        for num, line in data[page]:
            if re.match(r'\s*$', line):
                continue
            left_space = len(line) - len(line.lstrip())
            #if left_space:
            #    min_indent = min(min_indent, left_space)
            totals[left_space] += 1
        # Strip that much from every line
        #data[page] = [
        #    (num, re.sub('^' + (' ' * min_indent), '', line))
        #    for num, line in data[page]
        #]

        indent_totals[page], _ = totals.most_common(1)[0]

    return data

def parse_transcript(url, text):
    data = strip_line_numbers(url, text)
    indent_totals = {}
    data = remove_left_indent(data, indent_totals)

    indent = None
    speech = None
    interviewer = None
    state = 'text'
    date = None
    for page in data.keys():
        new_para_indent = indent_totals[page] + 2
        if '2024-09-17' in url and page == 83:
            new_para_indent += 3
        if '2024-09-18' in url and page == 28:
            new_para_indent += 3
        if '2024-09-23' in url and page in (3, 52):
            new_para_indent += 4

        for num, line in data[page]:
            # Okay, here we have a non-empty, non-page number, non-index line of just text
            # print(f'{page},{num:02d} {line}')

            # Empty line
            if re.match('\s*$', line):
                continue

            line = line.replace('**', '\*\*')
            line = line.replace('_', '\_')

            # Date at start
            m = re.match(' *(Mon|Tues|Wednes|Thurs|Fri)day,? \d+(nd|th)? (August|September|October|November|December|January|February|March|April|May|June|July),? 20[12][1234]\.?$', line)
            if m:
                date = line.strip() # datetime.strptime(line.strip(), '%A, %d %B %Y')
                continue

            if state == 'adjournment':
                state = 'text'
                if re.match(' *(.*)\)$', line):
                    speech.add_text(line.strip())
                    continue

            # Time/message about lunch/adjournments
            m = re.match(' *(\(.*\))$', line)
            if m:
                spkr = None
                if speech:
                    spkr = getattr(speech, 'speaker', None)
                    yield speech
                #try:
                    #line = m.group(1)
                    #if re.match('\(1[3-9]\.', line):
                    #    time_format = '(%H.%M %p)'
                    #else:
                    #    time_format = '(%I.%M %p)'
                    #time = datetime.strptime(line, time_format).time()
                    #yield Speech(speaker=None, text=line)
                #except:
                yield Speech(speaker=None, text=line)
                speech = Speech( speaker=spkr, text='' )
                continue

            # Multiline message about adjournment
            m = re.match('(?i) *\((The (hearing|Inquiry) adjourned|On behalf of)', line)
            if m:
                yield speech
                state = 'adjournment'
                speech = Speech( speaker=None, text=line.strip() )
                continue

            # Questions
            m = re.match('(?:Further question|Question|Examin)(?:s|ed) (?:from|by) (.*?)(?: \(continued\))?$', line.strip())
            ignore = ()
            if m and line.strip() not in ignore:
                yield speech
                speech = Section( heading=fix_heading(line), level=2)
                interviewer = fix_name(m.group(1))
                continue

            # Headings
            m = re.match('Statement by (.*)$|Focus Group Session [34]$|Housekeeping$|([A-Z ]*)$|Announcement re |Decision$', line.strip())
            ignore = ()
            if m and line.strip() not in ignore:
                spkr = getattr(speech, 'speaker', None)
                yield speech
                speech = Section( heading=fix_heading(line) )
                if spkr:
                    yield speech
                    speech = Speech( speaker=spkr, text='' )
                continue

            # Headings with names in
            m = re.match('(?:(?:Opening|Closing) (?:s|r|R)|S)(?:tatement|emarks) (?:by|from) ([A-Z ]*)(?:, QC)?(?: \(continued\))?$|Announcements by ([A-Z ]*)$|(?:Further s|S)ubmissions? by ([A-Z ]*)(?:, QC)?$|Reply by ([A-Z ]*)$|Witness statement of ([A-Zc ]*) adduced$|Pre-recorded (?:opening )?statement by ([A-Z ]*)', line.strip())
            if m:
                yield speech
                name = next(filter(None, m.groups()))
                line = line.replace(name, fix_name(name)).strip()
                speech = Section( heading=line )
                continue

            m = re.match('Statement of ([A-Z ]*) (?:read )?by ([A-Z ]*)', line.strip())
            if m:
                yield speech
                for i in range(2):
                    name = m.group(i+1)
                    line = line.replace(name, fix_name(name)).strip()
                speech = Section( heading=line )
                continue

            # Witness arriving
            m = re.match(" *((?:[A-Z]|Mr)(?:[A-Z0-9' ,-]|Mc|Mr|and)+?)(,?\s*\(.*\)|, (?:sworn|affirmed))$", line)
            if m:
                heading = fix_name(m.group(1).strip())
                if 'statement' not in line:
                    Speech.witness = heading
                narrative = '%s%s.' % (m.group(1), m.group(2))
                spkr = speech.speaker
                yield speech
                yield Section( heading=heading )
                yield Speech( speaker=None, text=narrative )
                if m.group(3):
                    speaker = fix_name(m.group(3))
                    speech = Speech( speaker=speaker, text='')
                else:
                    speech = Speech( speaker=spkr, text='' )
                continue

            # Question/answer (speaker from previous lines)
            m = re.match(' *([QA])\. (.*)', line)
            if m:
                yield speech
                if m.group(1) == 'A':
                    assert Speech.witness
                    speaker = Speech.witness
                    typ = 'answer'
                else:
                    assert interviewer
                    speaker = interviewer
                    typ = 'question'
                speech = Speech( speaker=speaker, text=m.group(2), typ=typ )
                continue

            # New speaker
            m = re.match(' *((?:[A-Z -]|Mc)+): (.*)', line)
            if m:
                yield speech
                speaker = fix_name(m.group(1))
                if speaker not in ('The Stenographer', 'The Witness'):
                    interviewer = speaker
                speech = Speech( speaker=speaker, text=m.group(2) )
                continue

            # New paragraph if indent at least 8 spaces
            m = re.match(' ' * new_para_indent, line)
            if m:
                speech.add_para(line.strip())
                continue

            # If we've got this far, hopefully just a normal line of speech
            speech.add_text(line.strip())

    yield speech

#name_fixes = { }
def fix_name(name):
    name = name.title()
    name = name.replace('Qc', 'QC').replace('Kc', 'KC')
    name = name.replace(' Of ', ' of ')
    name = name.replace('The Right Honourable ', '').replace(' Mp', '')
    # Deal with the McNames
    name = re.sub('Mc[a-z]', lambda mo: mo.group(0)[:-1] + mo.group(0)[-1].upper(), name)
    #s = name_fixes.get(s, s)
    # More than one name given, or Lord name that doesn't include full name
    #if ' and ' in name or (' of ' in name and ',' not in name):
    #    return name
    # Remove middle names
    name = re.sub('^(DAC|DS|Dr|Miss|Mrs|Mr|Ms|Baroness|Lord|Professor|Sir|The) (\S+ )(?:\S+ )+?(\S+)((?: [QK]C)?| of \S+)$', r'\1 \2\3\4', name)
    name = re.sub('^(?!DAC|DS|Dr|Miss|Mrs|Mr|Ms|Baroness|Lord|Professor|Sir|The)(\S+) (?!Court)(?:\S+ )+?(\S+)((?: [QK]C)?)$', r'\1 \2', name)
    return name

def fix_heading(s):
    s = string.capwords(s.strip())
    s = re.sub('-[a-z]', lambda m: m.group(0).upper(), s)
    rep = [ 'Kc', 'Uk', 'Qc' ]
    s = re.sub('|'.join(rep), lambda m: m.group(0).upper(), s)
    rep = [ 'Of', 'By', 'The', 'To', 'On', 'For', 'And', 'Re', 'About' ]
    s = re.sub('\\b' + '\\b|\\b'.join(rep) + '\\b', lambda m: m.group(0).lower(), s)
    return s

load_data()
parse_transcripts()
