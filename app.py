import sys

from flask import Flask, render_template, request
import requests

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


@app.route('/', methods = ['POST', 'GET'])
def hello_world():
    if request.method == 'POST':

        data = request.form
        steam_id = data.get('steamid')

        if not steam_id:
            return render_template(
                "steamid-form.html",
                error_message="Insert a valid Steam ID")

        try:
            wishlist = steam_request_wishlist(steam_id)
            wishlist_games = build_game_data(wishlist)
            update_demo_info(wishlist_games)

            sorted_games = list(wishlist_games.values())
            sorted_games.sort(key=lambda game: game.wishlist_priority)

            total_demos = sum([1 for game in sorted_games if game.demo_appid])
            return render_template("wishlist.html", game_list=sorted_games, total_demos=total_demos)
        except Exception as e:
            print(f"Something went wrong for steam ID {steam_id}: {e}")
            return render_template(
                "steamid-form.html",
                error_message=f"Something went wrong: {e}")
    else:
        return render_template("steamid-form.html")


def build_game_data(wishlist_json: dict):

    games = {}

    for game_id, game_data in wishlist_json.items():
        title = _get_key(game_data, 'name')
        wishlist_priority = _get_key(game_data, 'priority')
        if game_id:
            games[game_id] = Game(appid=game_id, title=title, wishlist_priority=wishlist_priority)

    return games


def update_demo_info(games: dict[str, Game]):
    gameids = list(games.keys())

    # Send requests for game demo data in batches of 50
    for ids in range(0, len(gameids), 50):
        current_batch = gameids[ids:ids + 50]
        demo_info_data = steam_request_demo_info(current_batch)
        if demo_info_data:
            _fill_in_demo_appid(games, demo_info_data)


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
    app.run()