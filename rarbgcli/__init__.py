import asyncio
import json
import os
import re
import sys
import time
from functools import partial
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import quote

import requests
from tqdm import tqdm

from .utils import download_tesseract

CATEGORY2CODE = {
    'movies': '48;17;44;45;47;50;51;52;42;46'.split(';'),
    'xxx': '4'.split(';'),
    'music': '23;24;25;26'.split(';'),
    'tvshows': '18;41;49'.split(';'),
    'software': '33;34;43'.split(';'),
    'games': '27;28;29;30;31;32;40;53'.split(';'),
    'nonxxx': '2;14;15;16;17;21;22;42;18;19;41;27;28;29;30;31;32;40;23;24;25;26;33;34;43;44;45;46;47;48;49;50;51;52;54'.split(
        ';'),
    '': '',
}

real_print = print
pprint = print if sys.stdout.isatty() else partial(print, file=sys.stderr)

HOME_DIRECTORY = os.environ.get('RARBGCLI_HOME', str(Path.home()))
PROGRAM_HOME = os.path.join(HOME_DIRECTORY, '.rarbgcli')
os.makedirs(PROGRAM_HOME, exist_ok=True)
COOKIES_PATH = os.path.join(PROGRAM_HOME, 'cookies.json')

CODE2CATEGORY = {}
for category, codes in CATEGORY2CODE.items():
    if category in ['movies', 'xxx', 'music', 'tvshows', 'software']:
        for code in codes:
            CODE2CATEGORY[code] = category


# Captcha solving taken from https://github.com/confident-hate/seedr-cli
def solve_captcha(threat_defence_url):
    from io import BytesIO

    import pytesseract
    from PIL import Image
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as ec
    from selenium.webdriver.support.ui import WebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager

    def img2txt():
        try:
            clk_here_button = driver.find_element_by_link_text('Click here')
            clk_here_button.click()
            time.sleep(10)
            WebDriverWait(driver, 10).until(ec.element_to_be_clickable((By.ID, 'solve_string')))
        except Exception:
            pass
        finally:
            element = driver.find_elements_by_css_selector('img')[1]
            location = element.location
            size = element.size
            png = driver.get_screenshot_as_png()
            x = location['x']
            y = location['y']
            width = location['x'] + size['width']
            height = location['y'] + size['height']
            im = Image.open(BytesIO(png))
            im = im.crop((int(x), int(y), int(width), int(height)))
            return pytesseract.image_to_string(im)

    options = Options()
    options.add_argument('--no-sandbox')
    options.add_argument('--headless')
    options.add_argument('--log-level=3')
    options.add_argument('--disable-logging')
    options.add_argument('--output=' + ('NUL' if sys.platform == 'win32' else '/dev/null'))

    # import get_chrome_driver  # no longer needed since ChromeDriverManager exists
    # chromedriver_path = get_chrome_driver.main(PROGRAM_HOME)
    driver = webdriver.Chrome(
        ChromeDriverManager(path=PROGRAM_HOME).install(),
        chrome_options=options,
        service_log_path=('NUL' if sys.platform == 'win32' else '/dev/null'),
    )
    pprint('successfully loaded chrome driver')

    driver.implicitly_wait(10)
    driver.get(threat_defence_url)

    if sys.platform == 'win32':
        pytesseract.pytesseract.tesseract_cmd = os.path.join(PROGRAM_HOME, 'Tesseract-OCR', 'tesseract')

    try:
        solution = img2txt()
    except pytesseract.TesseractNotFoundError:
        pprint('Tesseract not found. Downloading tesseract ...')
        download_tesseract(PROGRAM_HOME)
        solution = img2txt()

    text_field = driver.find_element_by_id('solve_string')
    text_field.send_keys(solution)
    try:
        text_field.send_keys(Keys.RETURN)
    except Exception as e:
        pprint(e)

    time.sleep(3)
    cookies = {c['name']: c['value'] for c in (driver.get_cookies())}
    driver.close()
    return cookies


def cookies_txt_to_dict(cookies_txt: str) -> dict:
    # SimpleCookie.load = lambda self, data: self.__init__(data.split(';'))
    cookie = SimpleCookie()
    cookie.load(cookies_txt)
    return {k: v.value for k, v in cookie.items()}


def cookies_dict_to_txt(cookies_dict: dict) -> str:
    return '; '.join(f'{k}={v}' for k, v in cookies_dict.items())


def deal_with_threat_defence_manual(threat_defence_url):
    real_print(
        f"""
    rarbg CAPTCHA must be solved, please follow the instructions bellow (only needs to be done once in a while):

    1. On any PC, open the link in a web browser: "{threat_defence_url}"
    2. solve and submit the CAPTCHA you should be redirected to a torrent page
    3. open the console (press F12 -> Console) and paste the following code:

        console.log(document.cookie)

    4. copy the output. it will look something like: "tcc; gaDts48g=q8hppt; gaDts48g=q85p9t; ...."
    5. paste the output in the terminal here

    >>>
    """,
        file=sys.stderr,
    )
    cookies = input().strip().strip("'").strip('"')
    cookies = cookies_txt_to_dict(cookies)

    return cookies


def deal_with_threat_defence(threat_defence_url):
    try:
        return solve_captcha(threat_defence_url)
    except Exception as e:
        if not sys.stdout.isatty():
            raise Exception(
                'Failed to solve captcha automatically, please rerun this command (without a pipe `|`) and solve it manually. This process only needs to be done once'
            ) from e

        pprint('Failed to solve captcha, please solve manually', e)
        return deal_with_threat_defence_manual(threat_defence_url)


def get_page_html(target_url, cookies):
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.122 Safari/537.36'}
    while True:
        r = requests.get(target_url, headers=headers, cookies=cookies)
        pprint('going to page', r.url, end=' ')
        if 'threat_defence.php' not in r.url:
            break
        pprint('\ndefence detected')
        cookies = deal_with_threat_defence(r.url)
        # save cookies to json file
        with open(COOKIES_PATH, 'w') as f:
            json.dump(cookies, f)

    data = r.text.encode('utf-8')
    return r, data, cookies


def extract_torrent_file(anchor, domain='rarbgunblocked.org'):
    return (
            'https://'
            + domain
            + anchor.get('href').replace('torrent/', 'download.php?id=')
            + '&f='
            + quote(anchor.contents[0] + '-[rarbg.to].torrent')
            + '&tpageurl='
            + quote(anchor.get('href').strip())
    )


def open_url(url):
    if sys.platform == 'win32':
        os.startfile(url)
    elif sys.platform in ['linux', 'linux2']:
        os.system('xdg-open ' + url)
    else:  # if mac os
        os.system('open ' + url)


async def open_torrentfiles(urls):
    for url in tqdm(urls, 'downloading', total=len(urls)):
        open_url(url)
        if len(urls) > 5:
            await asyncio.sleep(0.5)


def extract_magnet(anchor):
    # real:
    #     https://rarbgaccess.org/download.php?id=...&h=120&f=...-[rarbg.to].torrent
    #     https://rarbgaccess.org/download.php?id=...&      f=...-[rarbg.com].torrent
    # https://www.rarbgaccess.org/download.php?id=...&h=120&f=...-[rarbg.to].torrent
    # matches anything containing "over/*.jpg" *: anything
    regex = r'over\/(.*)\.jpg\\'
    trackers = 'http%3A%2F%2Ftracker.trackerfix.com%3A80%2Fannounce&tr=udp%3A%2F%2F9.rarbg.me%3A2710&tr=udp%3A%2F%2F9.rarbg.to%3A2710'
    try:
        hash_ = re.search(regex, str(anchor))[1]
        title = quote(anchor.get('title'))
        return f'magnet:?xt=urn:btih:{hash_}&dn={title}&tr={trackers}'
    except Exception:
        return ''


size_units = {
    'B': 1,
    'KB': 10 ** 3,
    'MB': 10 ** 6,
    'GB': 10 ** 9,
    'TB': 10 ** 12,
    'PB': 10 ** 15,
    'EB': 10 ** 18,
    'ZB': 10 ** 21,
    'YB': 10 ** 24,
}


def parse_size(size: str):
    number, unit = [string.strip() for string in size.strip().split()]
    return int(float(number) * size_units[unit])


def format_size(size: int, block_size=None):
    """automatically format the size to the most appropriate unit"""
    if block_size is None:
        for unit in reversed(list(size_units.keys())):
            if size >= size_units[unit]:
                return f'{size / size_units[unit]:.2f} {unit}'
    else:
        return f'{size / size_units[block_size]:.2f} {block_size}'


def dict_to_fname(d):
    # copy and sanitize
    white_list = {'limit', 'category', 'order', 'search', 'descending'}
    args_dict = {k: str(v).replace('"', '').replace(',', '') for k, v in sorted(vars(d).items()) if k in white_list}
    filename = json.dumps(args_dict, indent=None, separators=(',', '='), ensure_ascii=False)[1:-1].replace('"', '')
    return filename


def unique(dicts):
    seen = set()
    deduped = []
    for d in dicts:
        t = tuple(d.items())
        if t not in seen:
            seen.add(t)
            deduped.append(d)
    return deduped


def load_cookies(no_cookie):
    # read cookies from json file
    cookies = {}
    # make empty cookie if cookie doesn't already exist
    if not os.path.exists(COOKIES_PATH):
        with open(COOKIES_PATH, 'w') as f:
            json.dump({}, f)

    if not no_cookie:
        with open(COOKIES_PATH, 'r') as f:
            cookies = json.load(f)
    return cookies
