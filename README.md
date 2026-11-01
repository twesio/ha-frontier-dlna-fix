# Frontier DLNA Fix – Home Assistant add-on

Makes Frontier Silicon based internet radios (e.g. Kenwood CR-ST100S) usable as
DLNA players in [Music Assistant](https://music-assistant.io) when the radio's
DLNA device description lacks its `<serviceList>` and Music Assistant ignores
it.

The add-on announces a corrected copy of the radio on the network and relays
control requests to it. Audio goes directly from Music Assistant to the radio.

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**.
2. Open the **⋮** menu (top right) → **Repositories**.
3. Add `https://github.com/twesio/ha-frontier-dlna-fix` and close the dialog.
4. Install **Frontier DLNA Fix**, set `radio_host` on the **Configuration**
   tab and start it.

Full documentation (how it works, options, audio format, groups, troubleshooting):
[frontier_dlna_fix/DOCS.md](frontier_dlna_fix/DOCS.md), also shown on the
add-on's **Documentation** tab in Home Assistant.
