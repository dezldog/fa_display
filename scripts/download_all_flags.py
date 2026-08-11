#!/usr/bin/env python3
"""Download PNG flags (40px) from flagcdn.com for all ISO 3166-1 alpha-2 codes.
Saves files to ../flags/{cc}.png
"""
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

FLAGS_DIR = os.path.join(os.path.dirname(__file__), '..', 'flags')
os.makedirs(FLAGS_DIR, exist_ok=True)

# Full ISO 3166-1 alpha-2 country codes list
CODES = [
    'ad','ae','af','ag','ai','al','am','ao','aq','ar','as','at','au','aw','ax','az',
    'ba','bb','bd','be','bf','bg','bh','bi','bj','bl','bm','bn','bo','bq','br','bs','bt','bv','bw','by','bz',
    'ca','cc','cd','cf','cg','ch','ci','ck','cl','cm','cn','co','cr','cu','cv','cw','cx','cy','cz',
    'de','dj','dk','dm','do','dz',
    'ec','ee','eg','eh','er','es','et',
    'fi','fj','fk','fm','fo','fr',
    'ga','gb','gd','ge','gf','gg','gh','gi','gl','gm','gn','gp','gq','gr','gs','gt','gu','gw','gy',
    'hk','hm','hn','hr','ht','hu',
    'id','ie','il','im','in','io','iq','ir','is','it',
    'je','jm','jo','jp',
    'ke','kg','kh','ki','km','kn','kp','kr','kw','ky','kz',
    'la','lb','lc','li','lk','lr','ls','lt','lu','lv','ly',
    'ma','mc','md','me','mf','mg','mh','mk','ml','mm','mn','mo','mp','mq','mr','ms','mt','mu','mv','mw','mx','my','mz',
    'na','nc','ne','nf','ng','ni','nl','no','np','nr','nu','nz',
    'om',
    'pa','pe','pf','pg','ph','pk','pl','pm','pn','pr','ps','pt','pw','py',
    'qa',
    're','ro','rs','ru','rw',
    'sa','sb','sc','sd','se','sg','sh','si','sj','sk','sl','sm','sn','so','sr','ss','st','sv','sx','sy','sz',
    'tc','td','tf','tg','th','tj','tk','tl','tm','tn','to','tr','tt','tv','tw','tz',
    'ua','ug','um','us','uy','uz',
    'va','vc','ve','vg','vi','vn','vu',
    'wf','ws','ye','yt','za','zm','zw'
]

BASE = 'https://flagcdn.com/w40/{cc}.png'


def download_one(cc):
    url = BASE.format(cc=cc)
    out = os.path.join(FLAGS_DIR, f"{cc}.png")
    if os.path.exists(out) and os.path.getsize(out) > 100:
        return cc, 'cached'
    try:
        urllib.request.urlretrieve(url, out)
        return cc, 'ok'
    except Exception as exc:
        return cc, f'error: {exc}'


if __name__ == '__main__':
    print('Downloading flags into', FLAGS_DIR)
    with ThreadPoolExecutor(max_workers=8) as ex:
        for cc, status in ex.map(download_one, CODES):
            print(cc, status)
    print('Done')
