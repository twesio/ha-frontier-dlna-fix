# Changelog

## 1.2.0

- New option `force_mp3` (on by default): play commands asking for FLAC or
  WAV are rewritten to MP3, which the radio accepts. Setting the output codec
  in Music Assistant is no longer needed.
- Log errors returned by the radio (e.g. `714 Illegal MIME type`) to the
  add-on log.

## 1.1.0

- Fix the player disappearing in Music Assistant 2.10+. Its protocol linking
  identified the player by the Home Assistant host's IP and merged it into
  another player running there. The description now points `presentationURL`
  at the radio, so the player is identified by the radio's own IP and MAC.
- New device ID, so Music Assistant drops the cached wrong link. The radio
  shows up as a new player: set its **Output codec** to **MP3** again.

## 1.0.0

- First release.
