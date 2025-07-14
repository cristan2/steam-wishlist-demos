import sqlite3
import sys
import time
import datetime
from sqlite3 import IntegrityError

from flask import Flask, render_template, request
import requests
from requests import JSONDecodeError

app = Flask(__name__)


class Game:
    def __init__(self,
                 appid: str,
                 title: str,
                 wishlist_priority: int=sys.maxsize,
                 demo_appid: str = None):
        self.appid = appid
        self.title = title
        self.url = f"https://store.steampowered.com/app/{appid}/"
        self.wishlist_priority = wishlist_priority
        self.demo_appid = demo_appid

    def get_demo_str(self):
        return "HAS DEMO" if self.demo_appid else "no demo"

    def __str__(self):
        return f"{self.title} ({self.wishlist_priority}, {self.appid}, {self.get_demo_str()}, {self.url})"


# TODO index single game
# TODO index missing appids
# TODO index unreleased games/update released
# TODO handle multiple demos
# TODO track indexing in progress
# TODO index single game on the spot if missing

@app.route('/index-list', methods = ['POST', 'GET'])  # TODO delete GET?
def index_games_list():
    app_list_v2_endpoint = "https://api.steampowered.com/ISteamApps/GetAppList/v2"

    start = time.time()

    # get existing appids list
    db_con = sqlite3.connect("db/steam_games.db")
    db_cur = db_con.cursor()

    # select last index
    current_last_index = db_cur.execute("SELECT appid FROM app_list ORDER BY appid DESC LIMIT 1")
    current_last_index = current_last_index.fetchone()
    current_last_index = current_last_index[0]

    # select all indexes
    indexed_appids = db_cur.execute("SELECT appid FROM app_list")
    indexed_appids = indexed_appids.fetchall()
    indexed_appids = [appid[0] for appid in indexed_appids]
    db_con.close()

    # send request to steam for new list
    response = requests.get(url=app_list_v2_endpoint)
    response_json = response.json()

    steam_apps = response_json['applist']['apps']

    # filter steam list - diff from existing
    steam_apps_flat_no_duplicates_all = {app_item['appid']: app_item['name'] for app_item in steam_apps if app_item['name'] }
    set_diff_new_appids = set(steam_apps_flat_no_duplicates_all.keys()) - set(indexed_appids)
    steam_apps_flat_no_duplicates_all = { appid: steam_apps_flat_no_duplicates_all.get(appid) for appid in set_diff_new_appids if steam_apps_flat_no_duplicates_all.get(appid)}

    db_con = sqlite3.connect("db/steam_games.db")
    db = db_con.cursor()

    try:
        db.executemany("INSERT INTO app_list(appid, name) VALUES(?,?)", steam_apps_flat_no_duplicates_all.items())
        db_con.commit()
    except IntegrityError:
        print("Skipping duplicate")
        pass
    db_con.close()

    print(f"Done indexing in {round(time.time() - start, 2)} sec")

    db_con = sqlite3.connect("db/steam_games.db")
    db = db_con.cursor()
    res = db.execute("SELECT * from app_list")
    res = res.fetchall()
    db.close()
    print(f"Now there are {len(res)} appids in app_list")

    # return res
    return "ok"

@app.route('/index-details-bulk', methods = ['POST', 'GET'])  # TODO delete GET?
def index_games_details_bulk():

    consecutive_ok = 0
    consecutive_errors = 0

    while True:
        try:
            # TODO stop condition for when appids are exhausted
            result = index_games_details_new()
            consecutive_ok = consecutive_ok + 1
            consecutive_errors = 0
            print(result)

            if result == "Completed":
                break
            else:
                # sleep_sec = random.randint(5, 15)
                sleep_sec = 250
                print(f"### {datetime.datetime.now()}: sleeping for {sleep_sec} sec. before next batch after {consecutive_ok} successful runs...")
                time.sleep(sleep_sec)
        except Exception as e:
            consecutive_errors = consecutive_errors + 1
            sleep_sec = 30
            print(f"######### {datetime.datetime.now()}: Pausing bulk indexing because of error, waiting {sleep_sec/60} min (consecutive errors {consecutive_errors} after {consecutive_ok} successful runs) ##########")
            print(str(e))
            consecutive_ok = 0
            time.sleep(sleep_sec)
            # break

     # TODO add return value?

@app.route('/index-details-bulk-diff', methods = ['POST', 'GET'])  # TODO delete GET?
def index_games_details_bulk_diff():

    DB_BATCH_SIZE = 200

    appids_to_index = sorted(get_appids_to_index_diff())
    print(f"Found {len(appids_to_index)} appids to index")

    last_processed_appid = get_last_updated_diff_index()
    if last_processed_appid:
        appids_to_index = [appid for appid in appids_to_index if appid > last_processed_appid]
        print(f"Found last updated appid {last_processed_appid}, indexing remaining {len(appids_to_index)}")

    if not appids_to_index:
        return "🎉🎉🎉 Nothing to index!"

    start_index = 0

    print(f"Will process batch {len(appids_to_index)} appids")

    while True:
        end_index = min(len(appids_to_index), start_index + DB_BATCH_SIZE)
        next_batch = appids_to_index[start_index:end_index]

        print(f"processing batch {start_index} to {end_index}: {next_batch}")

        if not next_batch:
            print(f"🎉🎉🎉 Completed indexing for {len(appids_to_index)} appids")
            break

        try:
            interrupt_result = index_games_details_diff(next_batch)
            index_result = "OK" if not interrupt_result else f"interrupted at index {interrupt_result}"
            print(f"indexing result = {index_result}")

            if interrupt_result:
                print(f"will start next batch at index {start_index} + {interrupt_result}")
                start_index = start_index + interrupt_result
            else:
                print(f"will start next batch from current end_index {end_index}")
                start_index = end_index

            # print progress
            remaining_to_be_processed = len(appids_to_index) - start_index
            percentage_completed = round(start_index / len(appids_to_index) * 100, 2)
            time_remaining = remaining_to_be_processed / DB_BATCH_SIZE * 5 # how much time to process remaining batches of 200 appids which take about 5 min (1 min/req + 4 min pause)
            if time_remaining > 60:
                time_remaining_str = f"{round(time_remaining / 60, 2)} hours"
            else:
                time_remaining_str = f"{round(time_remaining, 2)} min"
            progress = f"Processed {start_index}/{len(appids_to_index)} appids ({percentage_completed}%), {remaining_to_be_processed} remaining (approx {time_remaining_str})"
            print(progress)

            # sleep
            sleep_sec = 250
            print(f"### {datetime.datetime.now()}: sleeping for {sleep_sec} sec. before next batch...")
            time.sleep(sleep_sec)
        except Exception as e:
            sleep_sec = 30
            print(f"######### {datetime.datetime.now()}: Pausing bulk indexing because of error, waiting {sleep_sec/60} min  ##########")
            print(str(e))
            time.sleep(sleep_sec)

     # TODO add return value?
    return "OK"

@app.route('/index-details-new', methods = ['POST', 'GET'])  # TODO delete GET?
def index_games_details_new():
    print(f"Indexing ========================================================================================")

    DB_BATCH_SIZE = 200

    app_info_endpoint = "https://store.steampowered.com/api/appdetails"

    db_con = sqlite3.connect("db/steam_games.db")
    db_cur = db_con.cursor()

    last_processed_index = db_cur.execute("SELECT last_processed_index FROM index_status WHERE status = 'game_details_indexing' LIMIT 1")
    last_processed_index = last_processed_index.fetchone()
    last_processed_index = last_processed_index[0]

    print(f"last_processed_index = {last_processed_index}")

    all_ids = db_cur.execute("SELECT appid FROM app_list WHERE appid > ? ORDER BY appid ASC LIMIT ?", [last_processed_index, DB_BATCH_SIZE])
    all_appids_to_index = all_ids.fetchall()

    all_appids_to_index = [appid[0] for appid in all_appids_to_index if appid[0]]

    if not all_appids_to_index:
        print(f"🎉🎉🎉 Completed indexing, last_processed_index = {last_processed_index} !!!!!!!")
        db_cur.execute("UPDATE index_status SET indexing_in_process = ? WHERE status = 'game_details_indexing'", [0])
        db_con.commit()
        db_con.close()
        return "Completed"
    else:
        db_cur.execute("UPDATE index_status SET indexing_in_process = ? WHERE status = 'game_details_indexing'", [1])
        db_con.commit()

    start = time.time()

    joined_values = []

    last_processed_index_to_update = last_processed_index

    interrupted_at_index = None

    print(f"Will index {len(all_appids_to_index)} appids from {all_appids_to_index[0]} to {all_appids_to_index[len(all_appids_to_index)-1]}")

    # TODO if len(all_appids_to_index == 0) - all games were indexed

    for idx, appid in enumerate(all_appids_to_index, start=1):
        print(f"~~~ {idx}: Requesting details for appid {appid} ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        appid_str = str(appid)
        endpoint = f"{app_info_endpoint}?appids={appid}"

        response = requests.get(url=endpoint)

        try:
            response_json = response.json()
        except JSONDecodeError as e:
            if response.status_code == 200:
                print(f"Skip indexing appid {appid_str}: invalid json: {str(e)}")
                continue
            else:
                print(f"Interrupting indexing at appid {appid}: got response code {response.status_code} {response}, no json de decode")
                interrupted_at_index = appid
                break

        if response.status_code == 429:
            print(f"Interrupting indexing at appid {appid} because of too many requests")
            interrupted_at_index = appid
            break

        elif not response_json:
            print(f"Interrupting indexing at appid {appid} because of no response")
            interrupted_at_index = appid
            break

        elif not response_json.get(appid_str):
            print(f"Skip indexing appid {appid}: no body for appid in response")

        elif not True == response_json.get(appid_str).get('success'):
            print(f"Skip indexing appid {appid_str}: no success")
            last_processed_index_to_update = appid

        elif not response_json.get(appid_str).get('data'):
            print(f"Skip indexing appid {appid}: no game data in response")

        else:
            try:
                data = response_json.get(appid_str).get('data')
                values = extract_game_details(appid_str, data)
                joined_values.append(values)
                last_processed_index_to_update = appid

            except KeyError as ke:
                print(f"Error reading data for appid {appid}: {ke}")
                interrupted_at_index = appid
                break
            except Exception as e:
                print(f"Interrupting indexing at appid {appid} because of {e}")
                interrupted_at_index = appid
                break

    print(f"------------------------------------------------")

    remaining_to_be_processed = db_cur.execute("SELECT count(appid) as total_processed from app_list where appid > ?", [last_processed_index_to_update])
    remaining_to_be_processed = remaining_to_be_processed.fetchone()
    remaining_to_be_processed = remaining_to_be_processed[0]

    total_to_be_processed = db_cur.execute("SELECT count(appid) as total_processed from app_list")
    total_to_be_processed = total_to_be_processed.fetchone()
    total_to_be_processed = total_to_be_processed[0]

    processed_so_far = db_cur.execute("SELECT count(appid) as total_processed from app_list where appid <= ?", [last_processed_index_to_update])
    processed_so_far = processed_so_far.fetchone()
    processed_so_far = processed_so_far[0]

    percentage_completed = round(processed_so_far / total_to_be_processed * 100, 2)

    time_remaining = remaining_to_be_processed / DB_BATCH_SIZE * 5 # how much time to process remaining batches of 200 appids which take about 5 min (1 min/req + 4 min pause)
    if time_remaining > 60:
        time_remaining_str = f"{round(time_remaining / 60, 2)} hours"
    else:
        time_remaining_str = f"{time_remaining} min"

    db_con.close()

    print(f"------------------------------------------------")

    progress = f"last processed index = {last_processed_index_to_update}, {processed_so_far}/{total_to_be_processed} processed ({percentage_completed}%), {remaining_to_be_processed} remaining (approx {time_remaining_str})"

    # add to DB ==============

    print(f"Will update DB for {len(joined_values)}/{DB_BATCH_SIZE} appids: {[vals[0] for vals in joined_values]}")

    # TODO if joined_values is empty:

    db_con = sqlite3.connect("db/steam_games.db")
    db = db_con.cursor()
    try:
        # TODO handle duplicates - ???
        db.executemany("INSERT INTO app_details VALUES(?,?,?,?,?,?,?,?,?,?,?)", joined_values)
        db.execute("UPDATE index_status SET last_processed_index = ? WHERE status = 'game_details_indexing'", [last_processed_index_to_update])
        db_con.commit()
    except IntegrityError as ie:
        print(f"Skipping insertion because of duplicate: {ie}")
        pass
    db_con.close()

    print(f"Done indexing details for {DB_BATCH_SIZE} appids in {time.time() - start} sec")

    if interrupted_at_index:
        print(f"Interrupted at index {interrupted_at_index}")
        raise Exception
    else:
        return (f"ok for {len(joined_values)}/{DB_BATCH_SIZE} appids (last {last_processed_index_to_update})"
            f"\n{progress}"
            f"\nTime taken: {round(time.time() - start, 2)} sec ")


def get_appids_to_index_diff():
    db_con = sqlite3.connect("db/steam_games.db")
    db_cur = db_con.cursor()

    # select all appids from appid_list
    list_appids = db_cur.execute("SELECT appid FROM app_list")
    list_appids = list_appids.fetchall()
    list_appids = [appid[0] for appid in list_appids]

    # select all appids from appid_details
    indexed_appids = db_cur.execute("SELECT appid FROM app_details")
    indexed_appids = indexed_appids.fetchall()
    indexed_appids = [appid[0] for appid in indexed_appids]

    all_appids_to_index = list(set(list_appids) - set(indexed_appids))

    if not all_appids_to_index:
        print(f"🎉🎉🎉 Nothing to index!")
        db_cur.execute("UPDATE index_status SET indexing_in_process = ? WHERE status = 'game_details_indexing'", [0])
        db_con.commit()
        db_con.close()
        return None
    else:
        db_cur.execute("UPDATE index_status SET indexing_in_process = ? WHERE status = 'game_details_indexing'", [1])
        db_con.commit()
        db_con.close()

    return all_appids_to_index

def get_last_updated_diff_index():
    db_con = sqlite3.connect("db/steam_games.db")
    db_cur = db_con.cursor()

    last_processed_index = db_cur.execute("SELECT last_processed_index FROM index_status WHERE status = 'diff_details_indexing' LIMIT 1")
    last_processed_index = last_processed_index.fetchone()
    last_processed_index = last_processed_index[0]

    db_con.close()
    return last_processed_index


@app.route('/index-details-diff', methods = ['POST', 'GET'])  # TODO delete GET?
def index_games_details_diff(all_appids_to_index_batch):

    print(f"Indexing ========================================================================================")

    app_info_endpoint = "https://store.steampowered.com/api/appdetails"

    start = time.time()

    joined_values = []

    last_processed_appid_to_update = get_last_updated_diff_index()
    interrupted_at_index = None

    # TODO if len(all_appids_to_index == 0) - all games were indexed

    for idx, batch_appid in enumerate(all_appids_to_index_batch, start=1):
        print(f"~~~ {idx}: Requesting details for appid {batch_appid} ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
        appid_str = str(batch_appid)
        endpoint = f"{app_info_endpoint}?appids={batch_appid}"

        response = requests.get(url=endpoint)

        try:
            response_json = response.json()
        except JSONDecodeError as e:
            if response.status_code == 200:
                print(f"Skip indexing appid {appid_str}: invalid json: {str(e)}")
                continue
            else:
                print(f"Interrupting indexing at appid {batch_appid} (index {idx}): got response code {response.status_code} {response}, no json de decode")
                interrupted_at_index = idx
                break

        if response.status_code == 429:
            print(f"Interrupting indexing at appid {batch_appid} (index {idx}) because of too many requests")
            interrupted_at_index = idx
            break

        elif not response_json:
            print(f"Interrupting indexing at appid {batch_appid} (index {idx}) because of no response")
            interrupted_at_index = idx
            break

        elif not response_json.get(appid_str):
            print(f"Skip indexing appid {batch_appid} (index {idx}): no body for appid in response")

        elif not True == response_json.get(appid_str).get('success'):
            print(f"Skip indexing appid {appid_str}: no success")
            last_processed_appid_to_update = batch_appid

        elif not response_json.get(appid_str).get('data'):
            print(f"Skip indexing appid {batch_appid} (index {idx}): no game data in response")

        else:
            try:
                data = response_json.get(appid_str).get('data')
                values = extract_game_details(appid_str, data)
                joined_values.append(values)
                last_processed_appid_to_update = batch_appid

            except KeyError as ke:
                print(f"Error reading data for appid {batch_appid} (index {idx}): {ke}")
                interrupted_at_index = idx
                break
            except Exception as e:
                print(f"Interrupting indexing at appid {batch_appid} (index {idx}) because of {e}")
                interrupted_at_index = idx
                break

    print(f"------------------------------------------------")

    print(f"Will update DB for {len(joined_values)} appids in current batch: {[vals[0] for vals in joined_values]}")

    db_con = sqlite3.connect("db/steam_games.db")
    db = db_con.cursor()
    try:
        db.executemany("INSERT INTO app_details VALUES(?,?,?,?,?,?,?,?,?,?,?)", joined_values)
        db.execute("UPDATE index_status SET last_processed_index = ? WHERE status = 'diff_details_indexing'", [last_processed_appid_to_update])
        db_con.commit()
    except IntegrityError as ie:
        print(f"Skipping insertion because of duplicate: {ie}")
        pass
    db_con.close()

    print(f"Done indexing details for {len(joined_values)} appids in current batch in {round(time.time() - start, 2)} sec")

    if interrupted_at_index:
        print(f"Interrupted at index {interrupted_at_index}")
        # raise Exception
        return interrupted_at_index
    else:
        print(f"Everything went OK for current batch")
        return None



def extract_game_details(appid_str, data):
    _appid = appid_str # data.get('steam_appid') # sometimes, the steam_appid in the body is different from the requested appid (e.g. appid 100)
    _name = data.get('name')
    _type = data.get('type')
    _short_description = data.get('short_description')
    _is_free = data.get('is_free')
    _image_url = data.get('capsule_imagev5', data.get('capsule_image', data.get('header_image')))
    _metacritic = data.get('metacritic').get('score') if data.get('metacritic') else None
    _is_released, _release_date = extract_release_date(data.get('release_date')) if data.get('release_date') else None
    _demo_appid = extract_demo_ids(data.get('demos')) if data.get('demos') else None
    _fullgame_appid = data.get('fullgame').get('appid') if data.get('fullgame') else None
    values = [_appid, _name, _type, _short_description, _is_free, _image_url, _metacritic, _is_released, _release_date, _demo_appid, _fullgame_appid]
    return values

def extract_release_date(release_info):
    is_released = 1 if False == release_info.get('coming_soon') else 0
    date = release_info.get('date')
    return is_released, date

def extract_demo_ids(demo_list):
    demo_ids = [str(demo_data.get('appid')) for demo_data in demo_list if demo_data.get('appid')]
    return ",".join(demo_ids)

@app.route('/', methods = ['POST', 'GET'])
def home_page():
    return render_template("steamid-form.html")


@app.route('/wishlist', methods = ['POST', 'GET'])
def hello_world():
    if request.method == 'POST' or request.method == 'GET':
        steam_id = 0
        if request.method == 'POST':

            data = request.form
            steam_id = data.get('steamid')

        elif request.method == 'GET':
            steam_id = request.args['steamid']

        print("steamid = {}", steam_id)

        if not steam_id:
            return render_template(
                "steamid-form.html",
                error_message="Insert a valid Steam ID")

        try:
            wishlist_ids = steam_request_wishlist_new(steam_id)
            wishlist_games = build_game_data_new(steam_id, wishlist_ids)

            sorted_games = list(wishlist_games.values())
            sorted_games.sort(key=lambda game: game.wishlist_priority)

            total_demos = sum([1 for game in sorted_games if game.demo_appid])
            return render_template("wishlist.html",
                                   game_list=sorted_games,
                                   total_demos=total_demos,
                                   steam_id=steam_id)
        except Exception as e:
            print(f"Something went wrong for steam ID {steam_id}: {e}")
            return render_template(
                "steamid-form.html",
                error_message=f"Something went wrong: {e}")

    else:
        return render_template("steamid-form.html")


def build_game_data_new(steam_id, wishlist_ids: dict):

    games = {}

    db_con = sqlite3.connect("db/steam_games.db")
    db_cur = db_con.cursor()

    # TODO check param limit dbcon.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    #   https://stackoverflow.com/questions/5766230/select-from-sqlite-table-where-rowid-in-list-using-python-sqlite3-db-api-2-0

    args_params = ','.join(['?']*len(wishlist_ids.keys()))
    query = f"SELECT appid, name, demo_appid FROM app_details WHERE appid IN ({args_params})"
    print(f"Getting wishlist games from DB with this query...")
    print(query)
    print(f"... and params ...")
    print(wishlist_ids.keys())
    all_ids = db_cur.execute(query, list(wishlist_ids.keys()))
    all_wishlist_games = all_ids.fetchall()

    db_con.close()

    if all_wishlist_games:
        for wishlist_game in all_wishlist_games:
            appid = wishlist_game[0] # .get('appid')
            title = wishlist_game[1] #.get('name')
            demo_appid = wishlist_game[2] #.get('demo_appid')
            wishlist_priority = wishlist_ids.get(appid)

            if appid:
                games[appid] = Game(appid=appid, title=title, wishlist_priority=wishlist_priority, demo_appid=demo_appid)

    if len(wishlist_ids) > len(games):
        remaining_games_appids = {game.appid:game.title for game in games.values()}
        wishlist_diff = set(wishlist_ids.keys()) - set(remaining_games_appids.keys())
        print(f"Missing info on {len(wishlist_diff)} games: {wishlist_diff}")

    print(f"Built a list of {len(games)} apps for profile {steam_id}")
    return games

def build_game_data(wishlist_json: dict):

    games = {}

    for game_id, game_data in wishlist_json.items():
        title = _get_key(game_data, 'name')
        wishlist_priority = _get_key(game_data, 'priority')
        if game_id:
            games[game_id] = Game(appid=game_id, title=title, wishlist_priority=wishlist_priority)

    return games


def update_demo_info(games):
    gameids = list(games.keys())

    # Send requests for game demo data in batches of 50
    for ids in range(0, len(gameids), 50):
        current_batch = gameids[ids:ids + 50]
        demo_info_data = steam_request_demo_info(current_batch)
        if demo_info_data:
            _fill_in_demo_appid(games, demo_info_data)


def steam_request_wishlist_new(steamid):
    if str(steamid).isnumeric():
        wishlist_endpoint = f"https://api.steampowered.com/IWishlistService/GetWishlist/v1?steamid={steamid}"
    else:
        raise Exception("Need to provide the steamID, not your steam profile name")

    '''
    {
      "response": {
        "items": [
          {
            "appid": 17470,
            "priority": 281,
            "date_added": 1593611362
          }
        ]
      }
    }
    '''

    error_message = None

    wishlist_appids = {}

    print(f"Calling wishlist endpoint for profile {steamid}")

    try:
        page_response = requests.get(wishlist_endpoint)
        page_response_json = page_response.json()

        if page_response_json:
            response = page_response_json.get('response')
            response_items = response.get('items')

            if response_items:
                wishlist_appids = {item['appid']:item['priority'] for item in response_items}
                print(f"Got {len(wishlist_appids)} apps for profile {steamid}")

            else:
                error_message = f"Error reading wishlist - no items in response. Make sure your wishlist is public."

        else:
            error_message = f"Error reading wishlist - no response or invalid json"

    except Exception as e:
        error_message = f"Error reading wishlist: {e}"

    if error_message:
        raise Exception(error_message)
    else:
        return wishlist_appids
    # ------------------------


def steam_request_wishlist(steamid):

    if str(steamid).isnumeric():
        wishlist_endpoint = f"https://store.steampowered.com/wishlist/profiles/{steamid}/wishlistdata"
    else:
        raise Exception("Need to provide the steamID, not your steam profile name")

    def is_private_profile(_json):
        return len(_json) == 1 and 'success' in _json.keys()

    def is_valid_wishlist_json(_json):
        return len(_json) > 0

    wishlist_pages = {}
    page = 0
    error_message = None

    # By default, the endpoint returns a limited number of wishlist results,
    # so we need to explicitly request each page until there are no more
    # but set a hard limit at 50 (approx. 4,500 games) so as not to make too many requests
    while page < 50:
        try:
            page_response = requests.get(f"{wishlist_endpoint}?p={page}")
            current_page_json = page_response.json()

            if is_private_profile(current_page_json):
                error_message = "Profile is private"
                break

            if is_valid_wishlist_json(current_page_json):
                page += 1
                wishlist_pages.update(current_page_json)

            else:
                break

        except Exception as e:
            error_message = f"Error reading wishlist: {e}"
            break

    if error_message:
        raise Exception(error_message)
    else:
        return wishlist_pages


def steam_request_demo_info(app_id_list):
    """
    Endpoint from https://github.com/IsThereAnyDeal/AugmentedSteam/issues/447#issuecomment-1280992376

    Sample request-response:
    curl -v https://store.steampowered.com/saleaction/ajaxgetdemoevents?appids[]=1284190
    {"success":1,"info":[{"appid":1284190,"demo_appid":1754850,"demo_package_id":0}]}

    curl -v "https://store.steampowered.com/saleaction/ajaxgetdemoevents?appids[]=1284190&appids[]=867210&appids[]=846030"
    {"success":1,"info":[{"appid":846030,"demo_appid":949730,"demo_package_id":0},{"appid":1284190,"demo_appid":1754850,"demo_package_id":0},{"appid":867210,"demo_appid":0,"demo_package_id":0}]}
    """
    demo_endpoint = "https://store.steampowered.com/saleaction/ajaxgetdemoevents"

    appids = [f"appids[]={appid}" for appid in app_id_list]
    app_id_params = "&".join(appids)

    response = requests.get(url=demo_endpoint, params=app_id_params)

    # TODO error handling
    try:
        data = response.json()
        return data['info']
    except Exception as e:
        return None


def _get_key(_game_data, key):
    try:
        return _game_data[key]
    except KeyError as e:
        return None


def _fill_in_demo_appid(games_dict, demo_info_data):
    for app_id_obj in demo_info_data:
        appid = _get_key(app_id_obj, 'appid')
        demo_appid = _get_key(app_id_obj, 'demo_appid')

        if demo_appid:
            game = games_dict[str(appid)]
            game.demo_appid = demo_appid


if __name__ == '__main__':
    # this port must correlate with `internal_port` from fly.toml
    app.run(port=8080, host='0.0.0.0')