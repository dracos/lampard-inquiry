import bs4
import datetime
import os
import re
import requests_cache
import subprocess
import urllib.parse
from fns import fetch_list, load_data, save_data

session = requests_cache.CachedSession(expire_after=86400*7)
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
})

BASE = 'https://lampardinquiry.org.uk/hearings/'

def fetch_hearings():
    fetch_list(BASE, 'li-ci-title-meta', fetch_hearing_page)

def fetch_hearing_page(item):
    link = item.h3.a['href']
    title = item.h3.text
    date = item.find(class_='li-date-value').text
    date = re.sub('(st|nd|rd|th) ', ' ', date)
    date = datetime.datetime.strptime(date.strip(), '%d %b %Y').date()
    filename_pdf = f'data/{date}-{title}.pdf'
    filename_txt = filename_pdf.replace('.pdf', '.txt')
    filename_out = filename_pdf.replace('.pdf', '.scraped.txt')

    url = urllib.parse.urljoin(BASE, link)
    META['urls'][str(date)] = url

    if os.path.exists(filename_out):
        return

    r = session.get(url)
    soup = bs4.BeautifulSoup(r.content, "html.parser")

    for vid in soup.find_all('iframe'):
        if 'google' in vid['src']: continue # GTM
        yt_id = re.search('(?:v%3D|youtu.be/|youtube.com/embed/)(.*?)(?:&|%26|\?)', vid['src']).group(1)
        yt_title = vid['title']
        if str(date) in META['videos'] and yt_id in [ x['id'] for x in META['videos'][str(date)] ]:
            continue
        META['videos'].setdefault(str(date), []).append({'title': yt_title, 'id': yt_id})

    for link in soup.find_all('a', class_='btn-download'):
        if 'Transcript' not in link.text and 'Lampard Inquiry 18 September 2024' not in link.text: continue
        txt_href = urllib.parse.urljoin(BASE, link['href'])
        print('Downloading', date, txt_href)
        with open(filename_pdf, 'wb') as fp:
            content = session.get(txt_href).content
            fp.write(content)

    subprocess.run(['pdftotext', '-layout', filename_pdf])

    if str(date) == '2024-09-11':
        with open(filename_txt, 'r') as fp:
            text = convert_four_up_pdf(fp.read(), date)
        with open(filename_out, 'w') as fp:
            fp.write(text)
    else:
        # Not 4-up
        with open(filename_txt, 'r') as fpI, open(filename_out, 'w') as fpO:
            fpO.write(fpI.read())


def convert_four_up_pdf(text, date):
    # Remove header/footer from all pages
    text = re.sub('\014? *(The )?Lampard Inquiry  *\d+ .*? 202\d', '', text)
    text = re.sub(' *\(\d+\) Pages \d+ - \d+', '', text)
    #text = re.sub('\xef\xbf\xbd', '', text)

    # Loop through, slurping up the pages by page number
    text_l, text_r = [], []
    pages = {}
    text = re.split('\r?\n', text)
    state = 'okay'

    for line in text:
        if re.match('\s*$', line): continue
        if re.match(r' ?1 +INDEX', line): break
        elif 'INDEX' in line: state = 'index'
        elif state == 'index' and re.match(' *(Opening statement|Closing remarks) by ', line): continue

        m = re.match(r' +(\d+)(?: +(\d+))? *$', line)
        if m:
            page_l = int(m.group(1))
            pages[page_l] = text_l
            if m.group(2) and len(text_r):
                page_r = int(m.group(2))
                pages[page_r] = text_r
            text_l, text_r = [], []
            if state == 'index':
                break
            continue

        # Left and right pages
        m = re.match(r' *(\d+)( .*?) + \1( .*)?$', line)
        if m:
            line_n = int(m.group(1))
            line_l = '       %s' % m.group(2).rstrip()
            line_r = '       %s' % m.group(3) if m.group(3) else ''
            text_l.append('%2d%s' % (line_n, line_l))
            text_r.append('%2d%s' % (line_n, line_r))
            continue

        # Offset index lines (2023-11-28)
        #if m := re.match(r' +Questions from .*?\.\.\.', line):
        #    continue

        # Just left page at the end
        m = re.match(r' ?(\d+)( .*)?$', line)
        line_n = int(m.group(1))
        line_l = '       %s' % m.group(2) if m.group(2) else ''
        text_l.append('%2d%s' % (line_n, line_l))

    # Reconstruct in page order for normal processing
    text = ''
    for num, page in sorted(pages.items()):
        for line in page:
            text += line + '\n'
        text += '    %d\n\014\n' % num
    return text


META = {
    'evidence': {},
    'videos': {},
    'urls': {},
}

load_data(META)
fetch_hearings()
save_data(META)
