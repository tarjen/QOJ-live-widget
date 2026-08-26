from flask import Flask, render_template, request
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import certifi
import cloudscraper
import os
import re
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
contest_problem_id_cache = {}


def load_qoj_cookie():
    cookie = os.environ.get('QOJ_COOKIE', '').strip()
    cookie_file = os.environ.get('QOJ_COOKIE_FILE', '').strip()
    if not cookie and cookie_file:
        try:
            with open(cookie_file, encoding='utf-8') as file:
                cookie = file.read().strip()
        except OSError:
            return
    if cookie:
        session.headers.update({'Cookie': cookie})


load_qoj_cookie()


def is_login_page(response):
    return '/login' in response.url or '<title>Login - QOJ.ac</title>' in response.text


def get_cell_text(cell):
    return ' '.join(cell.get_text(' ', strip=True).split())


def classify_submission(result):
    lower_result = result.lower()
    if 'accepted' in lower_result or re.search(r'\bac\b', lower_result):
        return 'accepted'
    if 'compiling' in lower_result or 'judging' in lower_result or 'waiting' in lower_result:
        return 'pending'
    if not result or result == '-':
        return 'unknown'
    return 'failed'


def find_header_index(headers, names):
    for name in names:
        for index, header in enumerate(headers):
            if name in header:
                return index
    return None


def submission_sort_key(submission):
    numbers = re.findall(r'\d+', submission.get('id', ''))
    if numbers:
        return (1, int(numbers[-1]))
    return (0, -submission.get('_row_order', 0))


def parse_submission_time(value):
    for time_format in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(value, time_format)
        except ValueError:
            continue
    return None


def filter_contest_submissions(submissions, problem_ids, start_time, end_time):
    start = parse_submission_time(start_time)
    end = parse_submission_time(end_time)
    filtered = []
    for submission in submissions:
        if problem_ids and submission.get('problem_id') not in problem_ids:
            continue
        submitted_at = parse_submission_time(submission.get('time', ''))
        if start and end:
            if submitted_at is None or not start <= submitted_at <= end:
                continue
            elapsed_seconds = int((submitted_at - start).total_seconds())
            hours, remainder = divmod(elapsed_seconds, 3600)
            minutes = remainder // 60
            submission['contest_time'] = f'{hours}:{minutes:02d}'
        else:
            submission['contest_time'] = submission.get('time', '')
        filtered.append(submission)
    return filtered


def infer_contest_start(submissions, problems):
    elapsed_by_problem = {
        problem.get('id'): problem.get('contest_time')
        for problem in problems
        if problem.get('id') and problem.get('contest_time')
    }
    candidates = []
    for submission in submissions:
        if submission.get('class') != 'accepted':
            continue
        elapsed = elapsed_by_problem.get(submission.get('problem_id'))
        submitted_at = parse_submission_time(submission.get('time', ''))
        match = re.fullmatch(r'(\d+):(\d{2})', elapsed or '')
        if submitted_at is None or match is None:
            continue
        candidates.append(
            submitted_at - timedelta(
                hours=int(match.group(1)),
                minutes=int(match.group(2)),
            )
        )
    if not candidates:
        return None

    candidates.sort()
    best_cluster = []
    for left, start in enumerate(candidates):
        cluster = []
        for candidate in candidates[left:]:
            if candidate - start > timedelta(minutes=2):
                break
            cluster.append(candidate)
        if len(cluster) > len(best_cluster) or (
            len(cluster) == len(best_cluster) and cluster[-1] > best_cluster[-1]
        ):
            best_cluster = cluster
    midpoint = best_cluster[len(best_cluster) // 2]
    return midpoint.replace(second=0, microsecond=0)


def display_submission_per_problem(submissions, limit):
    grouped = {}
    for submission in submissions:
        problem = submission.get('problem', '').strip()
        if not problem:
            continue
        key = problem.casefold()
        grouped.setdefault(key, []).append(submission)

    selected = []
    for attempts in grouped.values():
        attempts.sort(key=submission_sort_key)
        first_accepted = next(
            (submission for submission in attempts if submission.get('class') == 'accepted'),
            None,
        )
        chosen = first_accepted or attempts[-1]
        attempt_count = attempts.index(chosen) + 1 if first_accepted else len(attempts)
        chosen['attempts'] = attempt_count
        chosen['marker'] = f'+{attempt_count}' if first_accepted else f'-{attempt_count}'
        selected.append(chosen)

    return sorted(selected, key=submission_sort_key, reverse=True)[:limit]


def parse_submission_table(html):
    soup = BeautifulSoup(html, 'html.parser')
    submissions = []
    for table in soup.find_all('table'):
        rows = table.find_all('tr')
        if not rows:
            continue
        headers = [get_cell_text(cell).lower() for cell in rows[0].find_all(['th', 'td'])]
        header_text = ' '.join(headers)
        if 'submit' not in header_text and 'result' not in header_text and 'score' not in header_text:
            continue
        id_index = find_header_index(headers, ['id', '#'])
        problem_index = find_header_index(headers, ['problem', 'task'])
        result_index = find_header_index(headers, ['result', 'status', 'score'])
        time_index = find_header_index(headers, ['submit time', 'submission time', 'submitted', 'time'])
        language_index = find_header_index(headers, ['language', 'lang'])
        for row_order, row in enumerate(rows[1:]):
            cells = row.find_all('td')
            if len(cells) < 4:
                continue
            values = [get_cell_text(cell) for cell in cells]
            links = [
                link.get('href', '')
                for cell in cells
                for link in cell.find_all('a', href=True)
            ]
            submission = {
                'id': values[id_index] if id_index is not None and id_index < len(values) else values[0],
                'problem': values[problem_index] if problem_index is not None and problem_index < len(values) else values[1],
                'result': values[result_index] if result_index is not None and result_index < len(values) else '',
                'time': values[time_index] if time_index is not None and time_index < len(values) else '',
                'language': values[language_index] if language_index is not None and language_index < len(values) else '',
                'class': 'unknown',
                'url': next((href for href in links if '/submission/' in href), ''),
                '_row_order': row_order,
            }
            problem_link = next((href for href in links if '/problem/' in href), '')
            problem_match = re.search(r'/problem/(\d+)', problem_link)
            if problem_match is None:
                problem_match = re.match(r'#?(\d+)', submission['problem'])
            submission['problem_id'] = problem_match.group(1) if problem_match else ''
            if not submission['result']:
                for value in values:
                    lower_value = value.lower()
                    if any(word in lower_value for word in ['accepted', 'wrong answer', 'time limit', 'memory limit', 'runtime error', 'compile error', 'compiling', 'judging', 'waiting']):
                        submission['result'] = value
                        break
            if not submission['result'] and len(values) >= 3:
                submission['result'] = values[2]
            if not submission['time']:
                for value in reversed(values):
                    if ':' in value or '-' in value:
                        submission['time'] = value
                        break
            if not submission['language']:
                for value in values:
                    lower_value = value.lower()
                    if any(lang in lower_value for lang in ['c++', 'python', 'java', 'rust', 'go', 'kotlin', 'c#']):
                        submission['language'] = value
                        break
            submission['class'] = classify_submission(submission['result'])
            submissions.append(submission)
    return submissions


def get_contest_problem_ids(contestid):
    if contestid in contest_problem_id_cache:
        return contest_problem_id_cache[contestid]
    try:
        response = session.get(f'https://qoj.ac/contest/{contestid}', timeout=20)
        response.raise_for_status()
    except Exception:
        return []
    soup = BeautifulSoup(response.text, 'html.parser')
    problem_ids = []
    pattern = re.compile(rf'/contest/{re.escape(str(contestid))}/problem/(\d+)')
    for link in soup.find_all('a', href=True):
        match = pattern.search(link.get('href', ''))
        if match and match.group(1) not in problem_ids:
            problem_ids.append(match.group(1))
    if problem_ids:
        contest_problem_id_cache[contestid] = problem_ids
    return problem_ids


def get_submissions(
    contestid,
    player,
    problems=None,
    start_time='',
    end_time='',
    limit=18,
):
    problem_ids = {problem['id'] for problem in problems or [] if problem.get('id')}
    url = f'https://qoj.ac/submissions?submitter={player}'
    submissions = []
    for page in range(1, 6):
        try:
            response = session.get(f'{url}&page={page}', timeout=20)
            response.raise_for_status()
        except Exception:
            break
        if is_login_page(response):
            return {
                'error': 'Submissions require QOJ login cookie',
                'items': [],
            }
        page_submissions = parse_submission_table(response.text)
        if not page_submissions:
            break
        if problem_ids:
            page_submissions = [
                submission
                for submission in page_submissions
                if submission.get('problem_id') in problem_ids
            ]
        if not page_submissions and submissions:
            break
        submissions.extend(page_submissions)

    if submissions:
        start = parse_submission_time(start_time)
        if start is None:
            start = infer_contest_start(submissions, problems or [])
        if start is not None:
            start_time = start.strftime('%Y-%m-%d %H:%M:%S')
            end_time = (start + timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S')
        submissions = filter_contest_submissions(
            submissions,
            problem_ids,
            start_time,
            end_time,
        )
    if submissions:
        return {
            'items': display_submission_per_problem(submissions, limit),
            'source': url,
            'start_time': start_time,
            'end_time': end_time,
        }
    return {
        'items': [],
        'error': 'No submissions found',
    }


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
    contest_problem_ids = get_contest_problem_ids(contestid)
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
            contest_time_match = re.search(r'(\d+):(\d{2})', mytag)
            problem_position = i - 2
            problems.append(
                {
                    'index': problem_index,
                    'id': contest_problem_ids[problem_position]
                    if problem_position < len(contest_problem_ids)
                    else '',
                    'contest_time': contest_time_match.group(0)
                    if contest_time_match
                    else '',
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
    contest['submissions'] = get_submissions(
        contestid,
        player,
        problems=contest['problems'],
        start_time=contest.get('start_time', ''),
        end_time=contest.get('end_time', ''),
    )
    if contest['submissions'].get('start_time'):
        contest['start_time'] = contest['submissions']['start_time']
        contest['end_time'] = contest['submissions']['end_time']
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
