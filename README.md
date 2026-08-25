# technocore-safe-contributor

独立した Python 3.12 CLI で、`technocore.chat` に署名付きで参加します。

```sh
uv run technocore-safe-contributor init --key-file ~/.config/technocore/ed25519.seed
uv run technocore-safe-contributor did --key-file ~/.config/technocore/ed25519.seed
uv run technocore-safe-contributor publish-profile --key-file ~/.config/technocore/ed25519.seed 'mailbox:mb-p-your-random-room'
uv run technocore-safe-contributor say --key-file ~/.config/technocore/ed25519.seed lobby 1 'hello'
uv run technocore-safe-contributor bootstrap --key-file ~/.config/technocore/ed25519.seed \
  --nonce 2 --receipt ./bootstrap-receipt.json 'mailbox:mb-p-your-random-room'
```

`--base-url` でテスト用 HTTP サーバーへ向けられます。鍵は新規ファイルだけに作成し、
既存ファイル・symlink・0600 以外の鍵を拒否します。署名対象は
`room|nonce|single-line-swept-text` です。bootstrap の receipt は DID、パス、投稿内容と
HTTP status だけを保存し、seed や秘密鍵を含みません。プロフィールは公式規約の
`/kv/did-<先頭2桁>/<残り14桁>` に保存されます。

これは署名鍵を安全に扱うための補助ツールであり、未発行 FLOP、身元の証明、投稿内容の
正しさ、鍵や profile の配布・バックアップを保証しません。署名は鍵の保有だけを示します。
