# Radio Indie88 FM — V7 MP3 320 kbps

Versão preparada para Vercel com identificação por Shazam usando uma amostra MP3 real em `/tmp`.

## Formato da amostra

- Duração: 22 segundos por omissão
- Codec: MP3 / libmp3lame
- Bitrate: CBR 320 kbps
- Frequência: 44.1 kHz
- Canais: estéreo
- Binário FFmpeg: incluído pelo `imageio-ffmpeg`

## Variáveis opcionais

- `SHAZAM_SAMPLE_SECONDS`: entre 12 e 28
- `SHAZAM_MP3_BITRATE_KBPS`: entre 192 e 320
- `IDENTIFY_CACHE_SECONDS`: entre 20 e 180

## Diagnóstico

Abra `/api/health` depois do deploy. O build esperado é:

`indie88-vercel-v7-mp3-320k-20260612`
