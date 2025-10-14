import json

'''
Example SETTINGS:
{
    "name-sep" : "—",
    "telegram-api-token" : "***",
    "po-token-gvs": "***",
    "po-token-web": "***",
    "download-dir": "/var/www/***",
    "server-root-url": "https://server/mp3",
    "users-list": [],
}
'''
SETTINGS = json.load( open('settings.json') )

if not "name-sep" in SETTINGS:
    SETTINGS["name-sep"] = "—"