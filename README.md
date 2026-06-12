# Radio Indie88 FM — V8 (lógica M80 Ballads)

Esta versão usa a mesma lógica de identificação da aplicação M80 Ballads:

- uma única amostra MP3;
- 12 segundos;
- 128 kbps;
- mono;
- 44.1 kHz;
- filtros leves `highpass`, `lowpass` e `volume`;
- sem `loudnorm`;
- sem segunda captura automática;
- cliente Shazam limitado a duas tentativas curtas;
- pedido `POST /api/identify` sempre novo, sem cache de falhas.

## Estrutura

```text
app.py
requirements.txt
vercel.json
public/
templates/
```

## Diagnóstico

- `/api/health`
- `/api/identify-diagnostics`
- `/api/stream-check`
- `/api/sample` — descarrega a amostra MP3 que seria enviada ao Shazam

## Publicação

Coloca todos os ficheiros na raiz do repositório e faz um novo deploy no Vercel.
