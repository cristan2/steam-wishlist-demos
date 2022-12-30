import json
import requests

# JSON file exported by Augmented Steam
WISHLIST_DUMP_FILE = "wishlist.json"
WISHLIST_HTML_FILE = "wishlist.html"


# TODO get wishlist dynamically
#  see https://github-wiki-see.page/m/Revadike/InternalSteamWebAPI/wiki/Get-Wishlist-Data


def extract_app_id(gameid):
    """
    Extract and return the appid number from the gameid object,
    where gameid is a list like this: ['steam', 'app/867210']
    """

    if len(gameid) != 2:
        print(f"unknown gameid: {gameid}")
        return None

    app_id_str = gameid[1]
    if app_id_str[:3] != 'app':
        print(f"unknown appid string: {app_id_str}")
        return None

    try:
        return app_id_str[4:]
    except Exception as e:
        print(f"error extracting game_id: {e}")
        return None


def get_app_id(_game_data):
    try:
        app_id_str = _game_data['gameid']
        return extract_app_id(app_id_str)
    except KeyError as e:
        print(f"No gameid in {_game_data}")
        return None


def get_key(_game_data, key):
    try:
        return _game_data[key]
    except KeyError as e:
        print(f"No {key} in {_game_data}")
        return None


def request_demo_info(app_id_list):
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


def fill_in_demo_appid(game_ids_titles_urls, demo_info_data):
    for app_id_obj in demo_info_data:
        appid = get_key(app_id_obj, 'appid')
        demo_appid = get_key(app_id_obj, 'demo_appid')

        if demo_appid:
            game = game_ids_titles_urls[str(appid)]
            game.demo_appid = demo_appid


class Game:

    def __init__(self, wishlist_index: int, appid: str, title: str, url: str = None, demo_appid: str = None):
        self.wishlist_index = wishlist_index
        self.appid = appid
        self.title = title
        self.url = url
        self.demo_appid = demo_appid

    def get_demo_str(self):
        return "HAS DEMO" if self.demo_appid else "no demo"

    def __str__(self):
        return f"{self.title} ({self.wishlist_index}, {self.appid}, {self.get_demo_str()}, {self.url})"


def make_html(gamelist, exclude_non_demos=False):

    total_games = len(gamelist)
    total_demos = sum([1 for game in gamelist.values() if game.demo_appid])

    def make_row_html(game):
        if game.demo_appid:
            return f"<tr style='background-color: lime'>" \
                   f"<td>{game.wishlist_index}</td>" \
                   f"<td><a href='{game.url}'>{game.title}</a></td>" \
                   f"<td>{game.demo_appid}</td>" \
                   f"</tr>"
        elif not exclude_non_demos:
            return f"<tr>" \
                   f"<td>{game.wishlist_index}</td>" \
                   f"<td>{game.title} (<a href='{game.url}'>Store Page</a>)</td>" \
                   f"<td></td>" \
                   f"</tr>"
        else:
            return ""

    game_list_html = [make_row_html(game) for game in gamelist.values()]
    game_list_html = "\n".join(game_list_html)

    html_string = f"""
<!DOCTYPE html>
<html lang="en-US">
<head>
    <meta charset="UTF-8">
</head>
<body>
    <h1>Wishlist</h1>
    <h3>Total games: {total_games}</h3>
    <h3>Total demos: {total_demos}</h3>
    <table>
    <thead>
    <tr>
        <td style='border-bottom: 1px solid black'>No.</td>
        <td style='border-bottom: 1px solid black'>Title</td>
        <td style='border-bottom: 1px solid black'>Demo appid</td>
    </tr>
    </thead>
    <tbody>
        {game_list_html}
    </tbody>
    </table>
    </body>
    </html>
    """

    return html_string, total_demos


def main():

    exclude_non_demos = False

    with open(WISHLIST_DUMP_FILE, encoding='utf-8') as wishlist_file:
        wishlist = json.load(wishlist_file)
        wishlist_games = wishlist['data']

        game_ids_titles_urls = {}

        for index, game_data in enumerate(wishlist_games):
            game_id = get_app_id(game_data)
            title = get_key(game_data, 'title')
            url = get_key(game_data, 'url')
            if game_id:
                game_ids_titles_urls[game_id] = Game(index, appid=game_id, title=title, url=url)

        gameids = list(game_ids_titles_urls.keys())

        for ids in range(0, len(gameids), 50):
            current_batch = gameids[ids:ids + 50]
            demo_info_data = request_demo_info(current_batch)
            if demo_info_data:
                fill_in_demo_appid(game_ids_titles_urls, demo_info_data)
            else:
                list_of_games = [game.title for game in current_batch]
                print(f"No demo info for batch {ids}, games = {list_of_games}")

        html_string, total_demos = make_html(game_ids_titles_urls, exclude_non_demos)

        with open(WISHLIST_HTML_FILE, mode='w', encoding='utf-8') as wishlist_html:
            wishlist_html.write(html_string)

        print(f"Done: found {total_demos} games with demos out of {len(game_ids_titles_urls)} total games")


if __name__ == "__main__":
    main()
