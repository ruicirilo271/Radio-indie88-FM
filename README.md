# Radio Indie88 FM — Vercel sem FFmpeg

Esta versão usa `shazamio-core` diretamente sobre uma pequena amostra do stream. Não chama `ffmpeg`, `subprocess` nem executáveis externos.

## Estrutura obrigatória na raiz do repositório

```text
app.py
requirements.txt
vercel.json
.python-version
templates/index.html
public/style.css
public/script.js
public/default-cover.webp
```

Não coloques estes ficheiros dentro de outra pasta no GitHub. O ficheiro `app.py` deste pacote tem de substituir o `app.py` antigo existente na raiz.

## Publicar

1. Apaga ou substitui os ficheiros antigos do repositório.
2. Copia todo o conteúdo deste pacote para a raiz.
3. No Vercel abre Deployments e escolhe Redeploy.
4. Desativa a opção de usar a cache do build, quando for apresentada.
5. Abre `/api/health`.

A resposta correta contém:

```json
{
  "ffmpeg_required": false,
  "build": "indie88-vercel-sem-ffmpeg-v4-20260612",
  "recognizer": "shazamio-core/raw-stream-bytes"
}
```

Se a página ainda mencionar FFmpeg, a produção continua ligada ao código antigo ou o novo projeto está configurado com a Root Directory errada.
