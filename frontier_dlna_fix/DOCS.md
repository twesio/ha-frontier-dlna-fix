# Frontier DLNA Fix

Some Frontier Silicon based internet radios (seen on a Kenwood CR-ST100S with
firmware `ir-mmi-FS2340-0000-0082_V4.5.22`) announce a DLNA MediaRenderer, but
their device description (`http://<radio>:8080/dd.xml`) contains no
`<serviceList>`. UPnP clients such as Music Assistant therefore see no playback
capability and ignore the radio.

This add-on announces a corrected copy of the radio on the network and relays
control requests (play, stop, volume, status) to the radio. Audio streams and
status events go directly between Music Assistant and the radio.

## Configuration

| Option | Description |
|---|---|
| `radio_host` | IP address of the radio (give it a fixed IP in your router) |
| `radio_port` | Port of the radio's DLNA renderer, normally `8080` |
| `port` | Port this add-on listens on, default `8099` |
| `friendly_name` | Optional name for the player; defaults to the radio's own name |

After starting the add-on, the radio appears in Music Assistant's DLNA provider
within a few minutes. The radio's own (broken) entry stays ignored.

Home Assistant's DLNA Digital Media Renderer integration may also discover the
corrected device; you can add or ignore it.
