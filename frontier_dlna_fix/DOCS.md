# Frontier DLNA Fix

Makes Frontier Silicon based internet radios usable as DLNA players in
[Music Assistant](https://music-assistant.io) when the radio's DLNA device
description is broken.

## The problem

Some Frontier Silicon radios announce a DLNA MediaRenderer on the network and
the renderer itself works fine, but the device description they serve
(`http://<radio>:8080/dd.xml`) has no `<serviceList>`. UPnP clients read the
service list to find the playback and volume controls, so they conclude the
device cannot play anything. Music Assistant silently ignores it; with debug
logging enabled you will see:

```
Ignoring KENWOOD CR-ST100S - no play capability
```

The services do exist on the radio at the conventional paths
(`/AVTransport/control`, `/RenderingControl/control`, …), they are just not
listed. Firmware updates don't fix it for the affected models.

To check whether your radio is affected:

```sh
curl -s http://<radio-ip>:8080/dd.xml | grep -c serviceList
```

`0` means the service list is missing and this add-on should help.

## How it works

```
                   control (play/stop/volume/status)
Music Assistant  ───────────────►  add-on :8099  ───────────────►  radio :8080
      ▲                              │ serves corrected dd.xml
      │                              │ announces itself via SSDP
      │
      └──── audio stream and status events go directly ────►  radio
```

* It serves a corrected copy of the radio's description that includes the
  service list, under its own device ID.
* It answers SSDP searches and sends announcements, so Music Assistant
  discovers it like any other DLNA player.
* It relays control requests to the radio. This is required: UPnP clients
  refuse service URLs that point to a different host than the description, so
  a description-only fix is not possible.
* Audio is never relayed. Music Assistant hands the radio a stream URL and the
  radio fetches it directly; status events are also sent straight back to
  Music Assistant.

If the add-on stops, the player becomes unavailable in Music Assistant. The
radio itself keeps working normally.

## Setup

1. On the **Configuration** tab set `radio_host` to the radio's IP address.
2. Start the add-on and check the **Log** tab for a line like
   `Serving radio 192.168.x.x:8080 as uuid:… at http://…:8099/dd.xml`.
3. Make sure the **DLNA** provider is enabled in Music Assistant. The radio
   appears as a player within a few minutes.
4. In Music Assistant open **Settings → Players → (your radio)** and set
   **Output codec** to **MP3** (see below).

Give the radio a fixed IP address in your router (for a FRITZ!Box: *Home
Network → Network → device → Always assign this network device the same IPv4
address*), otherwise the add-on loses it after a DHCP change.

### Configuration options

| Option | Default | Description |
|---|---|---|
| `radio_host` | – | IP address of the radio (required) |
| `radio_port` | `8080` | Port of the radio's DLNA renderer |
| `port` | `8099` | Port the add-on listens on |
| `friendly_name` | radio's name | Optional player name shown in Music Assistant |

## Output codec: use MP3

Music Assistant streams FLAC to DLNA players by default. These radios only
accept MP3 and AAC and reject FLAC with UPnP error `714 Illegal MIME type`, so
nothing plays. Set the player's **Output codec** to **MP3** (AAC also works).
The setting may only be visible with advanced settings enabled.

## Groups

DLNA players cannot join Music Assistant **sync groups**; the DLNA provider has
no synchronised playback for any device, so the radio is not listed there.
Use a **Universal Group** instead. It accepts any players, but they play
independently and may be a few seconds apart.

## Tested devices

| Device | Firmware |
|---|---|
| Kenwood CR-ST100S | `ir-mmi-FS2340-0000-0082_V4.5.22.75d4f4-1A44` |

Other Frontier Silicon radios (Hama, Medion, Silvercrest, Auna, Revo, …) with
the same symptom will likely work too. Reports are welcome.

## Troubleshooting

* **Player does not appear:** check the add-on log for errors, confirm the
  radio is reachable from Home Assistant, and wait up to five minutes. Enabling
  *allow network scan for discovery* in the Music Assistant DLNA provider can
  help.
* **Player appears but nothing plays:** set the output codec to MP3 (see
  above).
* **Player vanished after a Music Assistant update (2.10+):** update this
  add-on to 1.1.0 or later. Older versions let Music Assistant identify the
  player by the Home Assistant host's IP, so it got merged into another player
  on that host. After updating, the radio appears as a new player; set its
  output codec to MP3 again.
* **Log says the radio is not reachable:** the radio is off at the mains or
  its IP changed. The add-on retries every 30 seconds.
* **Home Assistant shows a newly discovered DLNA device:** that is this
  add-on's corrected device. You can add it to Home Assistant or ignore it.

## Running without Home Assistant

The script uses only the Python standard library and can run on any always-on
Linux machine in the same network:

```sh
RADIO_HOST=192.168.x.x python3 sidecar.py
```

Set `DEBUG=1` for verbose logging.
