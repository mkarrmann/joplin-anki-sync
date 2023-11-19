import requests
import json
import re
import os
from collections import defaultdict

PYTHONHASHSEED = None

anki_origin = "http://localhost:8765/"
joplin_origin = "http://localhost:41184/"

token = ""
folders = {}
excluded_headers = ()
excluded_notes = ()
created = []
updated = []
deleted = []


def config_parser():
    global token
    global folders
    global excluded_headers
    global excluded_notes

    # Joplin web clipper authorization token parsing
    paths = (
        f'{os.getenv("HOME")}/.config/joplin-anki-sync/token.json',
        f'{os.getenv("PWD")}/token.json',
    )

    if os.path.isfile(paths[0]) or os.path.isfile(paths[1]):
        if os.path.isfile(paths[0]):
            path = paths[0]
        else:
            path = paths[1]
        with open(path) as config_file:
            try:
                token_json = json.load(config_file)
            except json.decoder.JSONDecodeError as error:
                print(
                    f"[Error] JSON decoder error: {error}. Please check '{path}'"
                    " syntax."
                )
                exit()
    else:
        print(
            f"[Error] At least one of the following files does not exist: '{paths}'."
            "Please read the manual :)"
        )
        exit()

    token = token_json["token"]

    # Configuration parsing
    config_json = ""
    paths = (
        f'{os.getenv("HOME")}/.config/joplin-anki-sync/config.json',
        f'{os.getenv("PWD")}/config.json',
    )

    if os.path.isfile(paths[0]) or os.path.isfile(paths[1]):
        if os.path.isfile(paths[0]):
            path = paths[0]
        else:
            path = paths[1]
        with open(path) as config_file:
            try:
                config_json = json.load(config_file)
            except json.decoder.JSONDecodeError as error:
                print(
                    f"[Error] JSON decoder error: {error}\nPlease check '{path}'"
                    " syntax."
                )
                exit()
    else:
        print(f"[Error] At least one of the following files does not exist: '{paths}'.")
        exit()

    try:
        response = requests.get(f"{joplin_origin}folders?token={token}")
        response_json = json.loads(response.text)
    except requests.exceptions.ConnectionError:
        msg = f"Cannot connect to Joplin web clipper service ({joplin_origin})"
        print(msg)
        exit()

    config_folers = set(config_json["folders"])

    # Assumes graph is a set of distinct trees
    graph = defaultdict(list)
    for joplin_folder in response_json["items"]:
        if joplin_folder["parent_id"] is not None:
            graph[joplin_folder["parent_id"]].append(joplin_folder)

    for joplin_folder in response_json["items"]:
        if joplin_folder["title"] in config_folers:
            stack, root = [joplin_folder], joplin_folder["title"]
            while stack:
                node = stack.pop()
                folders[f"{node['title']}"] = (
                    node["id"],
                    root,
                )
                if graph[node["id"]]:
                    stack.extend(graph[node["id"]])
    excluded_headers = tuple(config_json["exclude_headers"])
    excluded_notes = tuple(config_json["exclude_notes"])

    # At this moment, version check is used only for Error handling
    try:
        anki_json = {"action": "version", "version": 6}
        response = requests.post(anki_origin, json=anki_json)
    except requests.exceptions.ConnectionError:
        msg = f"Cannot connect to Ankiconnect add-on ({anki_origin})"
        print(msg)
        exit()


def joplin_note_parser(note_name, note_id):
    header_re = re.compile(r"^# .*", re.MULTILINE)
    response = requests.get(
        f"{joplin_origin}/notes/{note_id}?token={token}&fields=body"
    )
    response_json = json.loads(response.text)
    markdown = response_json["body"]
    headers = re.findall(header_re, markdown)
    headers_hash = {}

    # Removing code coments from headers
    check = False
    comment_headers = []
    for line in markdown.split("\n"):
        if re.search(r"^```", line):
            check = not check
        if check == True and re.search(r"^# .*", line):
            comment_headers.append(line)
    headers = list(set(headers) - set(comment_headers))

    for header in headers:
        if header.rstrip().startswith(excluded_headers):
            continue
        if "==" in header:
            continue
        content = None
        subheaders = []
        # TODO refactor this
        for line in markdown.split("\n"):
            if re.search(header_re, line):
                if content is not None and line not in comment_headers:
                    break
            if content is not None:
                if line.count("$") > 0 and line.count("$") % 2 == 0:
                    str_builder = []
                    in_formula = False
                    for char in line:
                        if char != "$":
                            str_builder.append(char)
                        else:
                            if in_formula:
                                str_builder.extend(["[", "/", "$", "]"])
                            else:
                                str_builder.extend(["[", "$", "]"])
                            in_formula = not in_formula
                    line = "".join(str_builder)
                content += line
                if re.search(r"^##+", line):
                    subheaders.append(re.sub(r"^##+ ", "", line))
            if header == line:
                content = ""
        title = (
            f"{note_name}  / {header.replace('# ', '')} {str(subheaders)}"
            if subheaders
            else f"{note_name} / {header.replace('# ', '')}"
        )
        headers_hash[title] = content
    return headers_hash


def joplin_folder_parser(folder_id):
    response = requests.get(
        f"{joplin_origin}/folders/{folder_id}/notes?token={token}"
    ).json()
    notes = {}
    for note in response["items"]:
        note_title = note["title"]
        notes[note_title] = note["id"]
    return notes


def anki_deck_parser(deck):
    anki_json = {
        "action": "findNotes",
        "version": 6,
        "params": {"query": f'deck:"{deck}"'},
    }
    response = requests.post(anki_origin, json=anki_json)
    cards_id = json.loads(response.text)["result"]
    cards = {}
    for card_id in cards_id:
        anki_json = {
            "action": "notesInfo",
            "version": 6,
            "params": {"notes": [card_id]},
        }
        response = requests.post(anki_origin, json=anki_json)
        note_json = json.loads(response.text)
        front = note_json["result"][0]["fields"]["Front"]["value"]
        back = note_json["result"][0]["fields"]["Back"]["value"]
        cards[front] = (
            card_id,
            back,
        )
    return cards


def anki_add_card(deck, front, back, cards):
    anki_json = {
        "action": "addNote",
        "version": 6,
        "params": {
            "note": {
                "deckName": deck,
                "modelName": "Basic",
                "fields": {"Front": front, "Back": back},
            }
        },
    }
    for card_id, card_info in cards.items():
        if card_info[0] == front:
            if card_info[1] != back:
                anki_json_d = {
                    "action": "deleteNotes",
                    "version": 6,
                    "params": {"notes": [card_id]},
                }
                response = requests.post(anki_origin, json=anki_json_d)
                response = requests.post(anki_origin, json=anki_json)
                updated.append(front)
            return
    response = requests.post(anki_origin, json=anki_json)
    created.append(front)


def anki_del_card(deck, titles, cards):
    exist = False
    for card_id, card_info in cards.items():
        for title in titles:
            if card_info[0] == title:
                exist = True
                break
        if not exist:
            anki_json = {
                "action": "notesInfo",
                "version": 6,
                "params": {"notes": [card_id]},
            }
            response = requests.post(anki_origin, json=anki_json)
            note_json = json.loads(response.content)
            front = note_json["result"][0]["fields"]["Front"]["value"]
            deleted.append(front)
            anki_json_d = {
                "action": "deleteNotes",
                "version": 6,
                "params": {"notes": [card_id]},
            }
            response = requests.post(anki_origin, json=anki_json_d)
        exist = False


config_parser()

anki_cards: dict = {}
joplin_notes: dict = {}
for f_name in folders.keys():
    f_id, root = folders[f_name]
    anki_cards |= anki_deck_parser(f_name)
    notes = joplin_folder_parser(f_id)
    for n_name, n_id in notes.items():
        if n_name.startswith(excluded_notes):
            continue
        new_notes = joplin_note_parser(n_name, n_id)
        joplin_notes |= {
            title: (
                content,
                root,
            )
            for title, content in new_notes.items()
        }

to_delete = set(anki_cards.keys()) - set(joplin_notes.keys())
requests.post(
    anki_origin,
    json={
        "action": "deleteNotes",
        "version": 6,
        "params": {"notes": [anki_cards[title][0] for title in to_delete]},
    },
)
print("Deleted notes:", len(to_delete))

to_add = set(joplin_notes.keys()) - set(anki_cards.keys())
r = requests.post(
    anki_origin,
    json={
        "params": {
            "notes": [
                {
                    "deckName": info[1],
                    "modelName": "Basic",
                    "fields": {"Front": front, "Back": info[0]},
                }
                for front, info in joplin_notes.items()
                if front in to_add
            ]
        },
        "action": "addNotes",
        "version": 6,
    },
)
print("Added notes:", len(to_add))

for title in set(anki_cards.keys()) & set(joplin_notes.keys()):
    if anki_cards[title][1] != joplin_notes[title][0]:
        anki_json = {
            "action": "updateNote",
            "version": 6,
            "params": {
                "note": {
                    "id": anki_cards[title][0],
                    "fields": {"Front": title, "Back": joplin_notes[title][0]},
                }
            },
        }
        response = requests.post(anki_origin, json=anki_json)
        updated.append(title)
print("Updated notes:", len(updated))
