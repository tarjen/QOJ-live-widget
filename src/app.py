from flask import Flask, render_template, request
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import certifi
import cloudscraper
import urllib3

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 创建requests会话，设置用户代理和SSL验证
session = cloudscraper.create_scraper(browser={
    'browser': 'chrome',
    'platform': 'windows',
    'mobile': False,
})
session.verify = certifi.where()
app = Flask(__name__, template_folder='../templates')


def get_contest_info(contestid, player):
    contest = {'problems': []}
    try:
        response = session.get(
            f'https://qoj.ac/results/QOJ{contestid}?player={player}',
            timeout=20,
        )
        response.raise_for_status()
    except Exception as exc:
        contest['error'] = f'Failed to load QOJ: {exc}'
        return contest

    soup = BeautifulSoup(response.text, 'html.parser')
    title = soup.find('title')
    if title and 'Just a moment' in title.text:
        contest['error'] = 'QOJ returned a Cloudflare challenge'
        return contest

    h2 = soup.find('h2')
    if h2 is not None:
        contest['name'] = h2.text.strip()
    for p in soup.find_all('p'):
        txt = p.text.strip()
        if txt.startswith('Current Time: '):
            current_time = txt[len('Current Time: '):].strip()  # e.g., '2:33:13'
            # Parse current_time as timedelta and subtract from now
            h, m, s = map(int, current_time.split(':'))
            delta = timedelta(hours=h, minutes=m, seconds=s)
            start_time = datetime.now() - delta
            end_time = start_time + timedelta(hours=5)
            contest['start_time'] = start_time.strftime('%Y-%m-%d %H:%M:%S')
            contest['end_time'] = end_time.strftime('%Y-%m-%d %H:%M:%S')

    headers = soup.find_all('th')
    mystanding = soup.find('tr', class_='solver')
    player_key = player.strip().lower()
    if mystanding is None and player_key:
        for row in soup.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) < 2:
                continue
            username_cell = cells[1]
            username_text = username_cell.get_text(' ', strip=True).lower()
            profile_links = [
                link.get('href', '').rstrip('/').split('/')[-1].lower()
                for link in username_cell.find_all('a', href=True)
            ]
            if player_key == username_text or player_key in profile_links:
                mystanding = row
                break

    if mystanding is not None:
        myrow = mystanding.find_all('td')
        problems = []
        last_problem_column = min(len(headers), len(myrow)) - 3
        for i in range(2, last_problem_column):
            problem_index = headers[i].get_text(' ', strip=True).split()[0]
            mystatus = myrow[i].get('class', ['stnd'])[0]
            mytag = myrow[i].get_text(' ', strip=True)
            problems.append(
                {
                    'index': problem_index,
                    'status': mystatus,
                    'tag': mytag
                }
            )
        solved = myrow[-3].text.strip()
        penalty = myrow[-2].text.strip()
        rank = myrow[0].text.strip()
        contest['rank'] = rank
        contest['problems'] = problems
        contest['solved'] = solved
        contest['penalty'] = penalty
    elif contest.get('name'):
        contest['error'] = f'Player "{player}" was not found in this standings page'
    return contest

@app.route('/overlay', methods=['GET'])
def overlay():
    if 'contest' not in request.args or 'player' not in request.args:
        return render_template('overlay.html', contest=None)
    contest = get_contest_info(request.args['contest'], request.args['player'])
    return render_template('overlay.html',
                           contest=contest,
                            player=request.args['player'],
                            contest_id=request.args['contest'])

@app.route('/overlay-update', methods=['GET'])
def overlay_update():
    if 'contest' not in request.args or 'player' not in request.args:
        return render_template('overlay_update.html', contest=None)
    contest = get_contest_info(request.args['contest'], request.args['player'])
    return render_template('overlay_update.html',
                           contest=contest,
                           player=request.args['player'],
                           contest_id=request.args['contest'])


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
