# prepper

This repo is the unattended card-judgement layer. It runs once a day, after the 14:00 UTC collector, and decides three supply cards, one ammo label, and whether Slack has anything to say.

The Mac app, Chrome helper, Railway collector, live SQLite, and retailer operations stay in Dropbox. They are not in this repo.

`judgement.py` is the contract those later runs import. It does not collect stock or prices, and it has no webhook URL and no tokens. A quiet day produces no Slack text. No automation is connected here yet.

The AEWS rule lives in the same module and is not part of the daily card pass. A level of 1 or 2 is quiet. Above 2, a message is due immediately and again every five minutes until a reply acknowledges that episode. The episode ends when the level falls to 2 or below. A later rise starts a new one. This module decides that timing. It does not download the dashboard and it does not hold a Slack token.
