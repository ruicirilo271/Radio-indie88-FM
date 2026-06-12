# Radio Indie88 FM — Vercel V6

Esta versão usa a mesma lógica de identificação que funcionou nas outras rádios:

1. `imageio-ffmpeg` fornece o binário FFmpeg dentro do pacote Python.
2. A Function grava uma amostra WAV PCM em `/tmp`.
3. O WAV é normalizado para mono, 44.1 kHz e enviado ao Shazam.
4. A capa é procurada no iTunes e, se falhar, usa a capa do Shazam ou a capa default.

## Publicação

Coloca todos os ficheiros na raiz do repositório e faz novo deploy sem alterar Build Command ou Output Directory.

## Testes

- `/api/health` deve mostrar `ffmpeg_available: true`.
- `/api/stream-check` deve mostrar `ok: true` e `bytes_received` maior que zero.
- `/api/identify?force=1` executa uma identificação completa.

## Variáveis opcionais

- `RADIO_STREAM`
- `RADIO_NAME`
- `SHAZAM_SAMPLE_SECONDS` (10 a 22, predefinição 18)
- `IDENTIFY_CACHE_SECONDS`
