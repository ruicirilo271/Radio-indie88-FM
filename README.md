# Radio Indie88 FM — Vercel V5

Esta versão corrige o diagnóstico apresentado durante a identificação.

A versão anterior continha no JavaScript uma mensagem que afirmava que o `app.py` era antigo sempre que o texto do erro incluía a palavra FFmpeg. Essa conclusão não era segura. Agora a aplicação:

- mostra o erro verdadeiro devolvido por `/api/identify`;
- só avisa sobre versões diferentes quando `config.build` e `data.build` existem e não coincidem;
- usa o identificador de build também no endereço do CSS e JavaScript para evitar cache antiga;
- continua a reconhecer bytes diretamente com `shazamio`, sem executar FFmpeg;
- mantém as capas quadradas e a capa default WebP.

## Estrutura na raiz do repositório

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

## Verificação depois do deploy

Abre:

```text
https://TEU-PROJETO.vercel.app/api/health?t=123
```

A resposta correta contém:

```json
{
  "ok": true,
  "build": "indie88-vercel-v5-20260612",
  "ffmpeg_required": false,
  "recognizer": "shazamio-core/raw-stream-bytes"
}
```

O parâmetro `?t=123` evita que uma resposta anterior guardada em cache confunda o teste.
