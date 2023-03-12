"""
rarbg, rarbccli - RARBG command line interface for scraping the rarbg.to torrent search engine
                  Outputs torrent information as JSON from a rarbg search.

Example usage:

    $ rarbgcli "the stranger things 3" --category movies --limit 10 --magnet | xargs qbittorrent

https://github.com/FarisHijazi/rarbgcli

"""
# TODO: turn this lib into an API lib (keep the CLI as a bonus)

import argparse
import asyncio
import datetime
import json
import os
import sys
from functools import partial
from urllib.parse import quote

import requests
import yaml
from bs4 import BeautifulSoup

from rarbgcli import CATEGORY2CODE, dict_to_fname, get_page_html, extract_magnet, extract_torrent_file, CODE2CATEGORY, \
    format_size, size_units, load_cookies, unique, open_torrentfiles, real_print, PROGRAM_HOME, parse_size


def get_user_input_interactive(torrent_dicts, start_index=0):
    header = ' '.join(
        ['SN'.ljust(4), 'TORRENT NAME'.ljust(80), 'SEEDS'.ljust(6), 'LEECHES'.ljust(6), 'SIZE'.center(12), 'UPLOADER'])
    choices = []
    for i in range(len(torrent_dicts)):
        torrent_name = str(torrent_dicts[i]['title'])
        torrent_size = str(torrent_dicts[i]['size'])
        torrent_seeds = str(torrent_dicts[i]['seeders'])
        torrent_leeches = str(torrent_dicts[i]['leechers'])
        torrent_uploader = str(torrent_dicts[i]['uploader'])
        choices.append(
            {
                'value': int(i),
                'name': ' '.join(
                    [
                        str(start_index + i + 1).ljust(4),
                        torrent_name.ljust(80),
                        torrent_seeds.ljust(6),
                        torrent_leeches.ljust(6),
                        torrent_size.center(12),
                        torrent_uploader,
                    ]
                ),
            }
        )
    choices.append({'value': 'next', 'name': 'next page >>'})

    import questionary
    from prompt_toolkit import styles

    prompt_style = styles.Style(
        [
            ('qmark', 'fg:#5F819D bold'),
            ('question', 'fg:#289c64 bold'),
            ('answer', 'fg:#48b5b5 bold'),
            ('pointer', 'fg:#48b5b5 bold'),
            ('highlighted', 'fg:#07d1e8'),
            ('selected', 'fg:#48b5b5 bold'),
            ('separator', 'fg:#6C6C6C'),
            ('instruction', 'fg:#77a371'),
            ('text', ''),
            ('disabled', 'fg:#858585 italic'),
        ]
    )
    answer = questionary.select(header + '\nSelect torrents', choices=choices, style=prompt_style).ask()
    return answer


def get_args():
    orderkeys = ['data', 'filename', 'leechers', 'seeders', 'size', '']
    sortkeys = ['title', 'date', 'size', 'seeders', 'leechers', '']
    parser = argparse.ArgumentParser(__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    # parser = parser.add_argument_group("Query")
    parser.add_argument('search', help='Search term')
    parser.add_argument('--category', '-c', choices=CATEGORY2CODE.keys(), default='nonxxx')
    parser.add_argument(
        '--domain',
        default='rarbgunblocked.org',
        help='Domain to search, you could put an alternative mirror domain here',
    )
    parser.add_argument(
        '--order',
        '-r',
        choices=orderkeys,
        default='',
        help='Order results (before query) by this key. empty string means no sort',
    )
    parser.add_argument(
        '--sort_order',
        '-o',
        choices=['asc', 'desc'],
        default=None,
        help='Sort order ascending or descending (only availeble with --order)',
    )

    output_group = parser.add_argument_group('Output options')
    output_group.add_argument('--magnet', '-m', action='store_true', help='Output magnet links')
    output_group.add_argument(
        '--sort',
        '-s',
        choices=sortkeys,
        default='',
        help='Sort results (after scraping) by this key. empty string means no sort',
    )
    output_group.add_argument('--limit', '-l', type=float, default='inf', help='Limit number of torrent magnet links')
    output_group.add_argument(
        '--interactive',
        '-i',
        action='store_true',
        default=None,
        help='Force interactive mode, show interctive menu of torrents',
    )
    output_group.add_argument(
        '--download_torrents',
        '-d',
        action='store_true',
        default=None,
        help='Open torrent files in browser (which will download them)',
    )
    output_group.add_argument(
        '--block_size',
        '-B',
        type=lambda x: x.upper(),
        metavar='SIZE',
        default=None,
        choices=list(size_units.keys()),
        help='Display torrent sizes in SIZE unit. Choices are: ' + str(set(list(size_units.keys()))),
    )

    misc_group = parser.add_argument_group('Miscilaneous')
    misc_group.add_argument('--no_cache', '-nc', action='store_true',
                            help="Don't use cached results from previous searches")
    misc_group.add_argument(
        '--no_cookie',
        '-nk',
        action='store_true',
        help="Don't use CAPTCHA cookie from previous runs (will need to resolve a new CAPTCHA)",
    )
    args = parser.parse_args()

    if args.interactive is None:
        args.interactive = sys.stdout.isatty()  # automatically decide based on if tty

    if args.limit < 1:
        print('--limit must be greater than 1', file=sys.stderr)
        exit(1)
    if args.sort_order is not None and not args.order:
        print('--sort_order requires --order', file=sys.stderr)
        exit(1)
    return args


def cli():
    args = get_args()
    print(vars(args))
    return main(**vars(args), _session_name=dict_to_fname(args))


def main(
        search,
        category='',
        download_torrents=None,
        limit=float('inf'),
        domain='rarbgunblocked.org',
        order='',
        sort_order=None,
        interactive=False,
        magnet=False,
        sort='',
        no_cache=False,
        no_cookie=False,
        block_size='auto',
        _session_name='untitled',  # unique name based on args, used for caching
):
    cookies = load_cookies(no_cookie)

    def print_results(dicts):
        if sort:
            dicts.sort(key=lambda x: x[sort], reverse=True)
        if limit < float('inf'):
            dicts = dicts[: int(limit)]

        for d in dicts:
            if not d['magnet']:
                print('fetching magnet link for', d['title'])
                try:
                    html_subpage = requests.get(d['href'], cookies=cookies).text.encode('utf-8')
                    parsed_html_subpage = BeautifulSoup(html_subpage, 'html.parser')
                    d['magnet'] = parsed_html_subpage.select_one('a[href^="magnet:"]').get('href')
                    d['torrent_file'] = parsed_html_subpage.select_one('a[href^="/download.php"]').get('href')
                except Exception as e:
                    print('Error:', e)

        # pretty print unique(dicts) as yaml
        print('torrents:', yaml.dump(unique(dicts), default_flow_style=False))

        # reads file then merges with new dicts
        with open(cache_file, 'w', encoding='utf8') as f:
            json.dump(unique(dicts), f, indent=4)

        # open torrent urls in browser in the background (with delay between each one)
        if download_torrents is True or interactive and input(
                f'Open {len(dicts)} torrent files in browser for downloading? (Y/n) ').lower() != 'n':
            torrent_urls = [d['torrent'] for d in dicts]
            magnet_urls = [d['magnet'] for d in dicts]
            asyncio.run(open_torrentfiles(torrent_urls + magnet_urls))

        if magnet:
            real_print('\n'.join([t['magnet'] for t in dicts]))
        else:
            real_print(json.dumps(dicts, indent=4))

    def interactive_loop(dicts):
        while interactive:
            os.system('cls||clear')
            user_input = get_user_input_interactive(dicts, start_index=len(dicts_all) - len(dicts_current))
            print('user_input', user_input)
            if user_input is None:  # next page
                print('\nNo item selected\n')
            elif user_input == 'next':
                break
            else:  # indexes
                input_index = int(user_input)
                print_results([dicts[input_index]])

            try:
                user_input = input('[ENTER]: back to results, [q or ctrl+C]: (q)uit')
            except KeyboardInterrupt:
                print('\nUser exit')
                exit(0)

            if user_input.lower() == 'q':
                exit(0)
            elif user_input == '':
                continue

    # == dealing with cache and history ==
    cache_file = os.path.join(PROGRAM_HOME, 'history', _session_name + '.json')
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    if os.path.exists(cache_file) and not no_cache:
        try:
            with open(cache_file, 'r') as f:
                cache = json.load(f)
        except Exception as e:
            print('Error:', e)
            os.remove(cache_file)
            cache = []
    else:
        cache = []

    dicts_all = []
    i = 1
    while True:  # for all pages
        target_url = 'https://{domain}/torrents.php?search={search}&page={page}'
        target_url_formatted = target_url.format(
            domain=domain.strip(),
            search=quote(search),
            page=i,
        )

        if sort_order:
            target_url_formatted += '&by=' + sort_order.upper().strip()
        if order:
            target_url_formatted += '&order=' + order.strip()
        if category:
            target_url_formatted += '&category=' + ';'.join(CATEGORY2CODE[category])

        r, html, cookies = get_page_html(target_url_formatted, cookies=cookies)

        with open(os.path.join(os.path.dirname(cache_file), _session_name + f'_torrents_{i}.html'), 'w',
                  encoding='utf8') as f:
            f.write(r.text)
        parsed_html = BeautifulSoup(html, 'html.parser')
        torrents = parsed_html.select('tr.lista2 a[href^="/torrent/"][title]')

        if r.status_code != 200:
            print('error', r.status_code)
            break

        print(f'{len(torrents)} torrents found')
        if len(torrents) == 0:
            break
        magnets = list(map(extract_magnet, torrents))
        torrentfiles = list(map(partial(extract_torrent_file, domain=domain), torrents))

        # removed torrents and magnet links that have empty magnets, but maintained order
        torrents, magnets, torrentfiles = zip(*[[a, m, d] for (a, m, d) in zip(torrents, magnets, torrentfiles)])
        torrents, magnets, torrentfiles = list(torrents), list(magnets), list(torrentfiles)

        dicts_current = [
            {
                'title': torrent.get('title'),
                'torrent': torrentfile,
                'href': f"https://{domain}{torrent.get('href')}",
                'date': datetime.datetime.strptime(
                    str(torrent.findParent('tr').select_one('td:nth-child(3)').contents[0]), '%Y-%m-%d %H:%M:%S'
                ).timestamp(),
                'category': CODE2CATEGORY.get(
                    torrent.findParent('tr').select_one('td:nth-child(1) img').get('src').split('/')[-1].replace(
                        'cat_new', '').replace('.gif', ''),
                    'UNKOWN',
                ),
                'size': format_size(parse_size(torrent.findParent('tr').select_one('td:nth-child(4)').contents[0]),
                                    block_size),
                'seeders': int(torrent.findParent('tr').select_one('td:nth-child(5) > font').contents[0]),
                'leechers': int(torrent.findParent('tr').select_one('td:nth-child(6)').contents[0]),
                'uploader': str(torrent.findParent('tr').select_one('td:nth-child(8)').contents[0]),
                'magnet': magnet,
            }
            for (torrent, magnet, torrentfile) in zip(torrents, magnets, torrentfiles)
        ]

        dicts_all += dicts_current

        cache = list(unique(dicts_all + cache))

        if interactive:
            interactive_loop(dicts_current)

        if len(list(filter(None, torrents))) >= limit:
            print(f'reached limit {limit}, stopping')
            break
        i += 1

    if not interactive:
        dicts_all = list(unique(dicts_all + cache))
        print_results(dicts_all)


if __name__ == '__main__':
    exit(cli())
