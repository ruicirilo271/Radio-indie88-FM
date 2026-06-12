# Radio Indie88 FM — V9 (metadados oficiais)

Esta versão deixa de enviar ao Shazam a mensagem de indisponibilidade devolvida pelo stream ao servidor do Vercel.

A identificação passa a consultar o player oficial da Indie88 e extrai diretamente:

- hora oficial;
- artista;
- título da música;
- capa e álbum através do iTunes, quando disponíveis.

## Vantagens

- identificação rápida;
- sem FFmpeg;
- sem gravações em `/tmp`;
- sem Shazam;
- menos memória e menor duração da Function;
- atualização automática a cada 30 segundos;
- histórico e Top 10 continuam no `localStorage` do navegador.

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
- `/api/official-now-playing`
- `/api/identify`
- `/api/stream-check` — verifica apenas se chegam bytes; não confirma que sejam música
- `/api/sample` — explica por que a antiga amostra foi desativada

## Publicação

Substitui todos os ficheiros na raiz do repositório e faz um novo deploy no Vercel.
