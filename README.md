# prepper

This repo is the unattended card-judgement layer. It runs once a day, after the 14:00 UTC collector, and decides three supply cards, one ammo label, and whether Slack has anything to say.

The Mac app, Chrome helper, Railway collector, live SQLite, and retailer operations stay in Dropbox. They are not in this repo.

`judgement.py` is the contract those later runs import. It does not collect stock or prices, and it has no webhook URL and no tokens. A quiet day produces no Slack text. No automation is connected here yet.
