import os
import requests
import subprocess
import json
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# Get secrets from Heroku environment
GOOGLE_SECRET = os.environ.get('GOOGLE_SECRET')
GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')
CHROMEDRIVER_PATH = os.environ.get('CHROMEDRIVER_PATH', '/usr/local/bin/chromedriver')
GOOGLE_CHROME_BIN = os.environ.get('GOOGLE_CHROME_BIN', '/usr/bin/google-chrome')

# Connect to google
scope = ['https://spreadsheets.google.com/feeds']
creds_dict = json.loads(GOOGLE_SECRET)
creds_dict["private_key"] = creds_dict["private_key"].replace("\\\\n", "\n")
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)

# Set up web browser
options = Options()
options.binary_location = GOOGLE_CHROME_BIN
options.add_argument('--disable-gpu')
options.add_argument('--no-sandbox')
options.headless = True

# Configurations
SCROLL_WAIT = 5
PAGES = ['icablenews', 'now.comNews', 'RTHKVNEWS', 'hk.nextmedia', 'standnewshk', \
        'inmediahk', 'hongkongfp', 'theinitium', 'hk01.news', 'onccnews', 'socrec', \
        'atvhongkong', 'scmp', 'am730hk', 'passiontimes', 'maddog.hk', \
        '376552872550155', 'TMHK.ORG']
HKG_TIME = timezone(timedelta(hours=8))
NCE_START = datetime(2019,6,9, tzinfo=HKG_TIME)

def get_title_desc_soup(vid_url):
    """
    Take facebook video url, return video title and BS element containing video descriptions.
    """
    vid_page = requests.get(vid_url).text
    vid_soup = BeautifulSoup(vid_page, "lxml")
    code = vid_soup.find_all("code")[1]
    
    if code.string is None:
        return None, None

    else:
        desc_soup = BeautifulSoup(code.string, "lxml")
        vid_title = vid_soup.find('meta', property="og:title").attrs['content']
        if vid_title == '':
            try:
                try:
                    vid_title = desc_soup.find('div', class_='userContent').p.br.previous_sibling
                except:
                    vid_title = desc_soup.find('div', class_='userContent').p.text
            except:
                vid_title = None

        return vid_title, desc_soup

def is_live_video(desc_soup):
    """
    Take BS element containing video desciptions, determine whether video was live.
    """
    if desc_soup is None:
        return False
    
    try:
        live_parent_tag = desc_soup.find('span', class_='fwn fcg')
        if live_parent_tag.find('span', class_='fwb fcg') is None:
            was_live = True
        else:
            was_live = False
    except:
        was_live = False
        
    return was_live

def get_start_time(desc_soup):
    """
    Take BS element containing video desciptions, return video start time
    """
    HKG_TIME = timezone(timedelta(hours=8))
    stime = desc_soup.find('span', class_="timestampContent").parent.attrs['data-utime']
    vid_start = datetime.fromtimestamp(float(stime), HKG_TIME)

    return vid_start

def get_video_duration(vid_src):
    """
    Take mp4 video url, return video duration.
    """
    cmd_args = ['ffprobe',
        '-v', 'error', 
        '-i', '{}'.format(vid_src),
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1']
    duration = subprocess.check_output(cmd_args)

    return duration

def get_end_time(vid_start, video_src):
    """
    Take video start time adn mp4 video url, return video end time.
    """
    HKG_TIME = timezone(timedelta(hours=8))
    
    duration = get_video_duration(video_src)
    
    stime = datetime.timestamp(vid_start)
    etime = stime + float(duration)
    vid_end = datetime.fromtimestamp(etime, HKG_TIME)

    return vid_end

def get_vid_id_src(link):
    """
    Take hyperlink from mobile video gallery, return video id and mp4 video url.
    """
    try:
        q = urlparse(link['href']).query
        q_parsed = parse_qs(q)
        video_id = q_parsed['id'][0]
        if 'src' in q_parsed.keys():
            video_src = q_parsed['src'][0]
        else:
            video_src = None
    except:
        q = link.parent.attrs['data-store']
        q_parsed = json.loads(q)
        video_id = q_parsed['videoID']
        
        video_src = q_parsed['src']
        if video_src == '':
            video_src = None

    return video_id, video_src

def get_vid_links(grid_soup):
    """
    Take BS element containing mobile video gallery, return list of hyperlinks to videos.
    """
    links = grid_soup.find('div', id='root').find_all('a')
    if len(links) == 0:
        links = grid_soup.find('div', id='root').find_all('i')
    return links

def update_database():

    print('Current time:', datetime.now(HKG_TIME))

    # Open google sheet
    client = gspread.authorize(creds)
    sheet = client.open_by_key(GOOGLE_SHEET_ID).worksheet('Sheet2')
    existing_ids = sheet.col_values(6)[1:]

    # Loop through facebook pages
    for page in PAGES:
        print('Checking page:', page)

        mobile_url = 'https://m.facebook.com/{}/video_grid/'.format(page)
        full_url = 'https://www.facebook.com/{}/videos/'.format(page)
        
        # Open browser
        driver = webdriver.Chrome(executable_path=CHROMEDRIVER_PATH , chrome_options=options)
        driver.get(mobile_url)

        # Infinite scroll until conditions are fulfilled
        n = 0
        need_to_scroll_more = True
        last_scroll_position = datetime.now()  # Initialize
        while need_to_scroll_more:        
            
            # Optain visible links
            vid_grid = driver.page_source
            grid_soup = BeautifulSoup(vid_grid, "lxml")
            links = get_vid_links(grid_soup)
            
            # Get start time from the oldest visible video
            last_video_index = len(links) -1
            continue_to_read_time = True
            while continue_to_read_time:
                if last_video_index >=0:
                    oldest_video_id, _ = get_vid_id_src(links[last_video_index])
                    oldest_vid_url = full_url + oldest_video_id
                    _, desc_soup = get_title_desc_soup(oldest_vid_url)

                    if is_live_video(desc_soup):
                        try:
                            if oldest_video_id in existing_ids:
                                need_to_scroll_more = False
                                print('No more scolling: Videos already in database.')

                            oldest_vid_start_time = get_start_time(desc_soup)
                            print(oldest_vid_start_time)

                            if oldest_vid_start_time == last_scroll_position:
                                need_to_scroll_more = False
                                print('No more scolling: End of page.')

                            elif oldest_vid_start_time < NCE_START:
                                need_to_scroll_more = False
                                print('No more scolling: Videos too old.')

                            last_scroll_position = oldest_vid_start_time
                            continue_to_read_time = False
                        except:
                            last_video_index -= 1
                    else:
                        last_video_index -= 1
                else:
                    continue_to_read_time = False
                    need_to_scroll_more = True
                
            # Continue scrolling if necessary
            if need_to_scroll_more:  
                driver.execute_script('window.scrollTo(0, document.body.scrollHeight);')
                n += 1
                print('Page scroll', n)

            time.sleep(SCROLL_WAIT)
            
        print('Found links:', len(links))
        
        # Loop through visible links on page
        for i, link in enumerate(links[:-1]):

            video_id, video_source = get_vid_id_src(link)
            vid_url = full_url + video_id

            if video_id in existing_ids:
                print('Video already in database.')

            elif video_source is None:
                print('Video is live now.')

            else:
                vid_title, desc_soup = get_title_desc_soup(vid_url)
                
                # Get info of live videos
                if is_live_video(desc_soup):
                    print(i, vid_title)
                    print(vid_url)

                    if vid_title is None:
                        vid_title = page

                    try:
                        vid_start = get_start_time(desc_soup)
                    except:
                        vid_start = 0
                    
                    if vid_start >= NCE_START:
                        print('Start:', vid_start.year, vid_start.month, vid_start.day, \
                            vid_start.hour, vid_start.minute, vid_start.second)

                        try:
                            vid_end = get_end_time(vid_start, video_source)
                            print('End:', vid_end.year, vid_end.month, vid_end.day, \
                                vid_end.hour, vid_end.minute, vid_end.second)

                            formatted_link = '<a href="https://www.facebook.com/{}/videos/{}" target="_blank">'.format(page, video_id) \
                                + 'https://www.facebook.com/{}/videos/{}</a>'.format(page, video_id)
                            
                            row = ['{}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}'.format(\
                                vid_start.year, vid_start.month, vid_start.day, \
                                vid_start.hour, vid_start.minute, vid_start.second), \
                                '{}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}'.format(\
                                vid_end.year, vid_end.month, vid_end.day, \
                                vid_end.hour, vid_end.minute, vid_end.second), \
                                vid_title, page, formatted_link, video_id]

                            sheet.append_row(row, value_input_option='USER_ENTERED')
                        except:
                            pass
                            
                    else:
                        print('video is too old.')
                        break

        driver.close()

if __name__ == '__main__':
    update_database()
