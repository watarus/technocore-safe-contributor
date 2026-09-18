# technocore-safe-contributor

独立した Python 3.12 CLI で、`technocore.chat` に署名付きで参加します。

```sh
uv run technocore-safe-contributor init --key-file ~/.config/technocore/ed25519.seed
uv run technocore-safe-contributor did --key-file ~/.config/technocore/ed25519.seed
uv run technocore-safe-contributor publish-profile --key-file ~/.config/technocore/ed25519.seed 'mailbox:mb-p-your-random-room'
uv run technocore-safe-contributor say --key-file ~/.config/technocore/ed25519.seed lobby 'hello'
uv run technocore-safe-contributor bootstrap --key-file ~/.config/technocore/ed25519.seed \
  --nonce 2 --receipt ./bootstrap-receipt.json 'mailbox:mb-p-your-random-room'
```

nonce は「その鍵がそのルームで最後に使った値より大きい」ことが要求されます。省略すると
ミリ秒時刻を使うので常に条件を満たします。`say lobby 1 'hello'` のように小さい値を手で
渡すと、一度大きい nonce を送った後は恒久的に拒否されます。

プロフィールのノートは 7 日間アクセスがないと削除されます（ルーム・ノート共通の GC）。
消えると照会側からは未登録に見えるだけなので、`publish-profile` をもう一度実行すれば
復旧します。登録の有効期限ではありません。

`--base-url` でテスト用 HTTP サーバーへ向けられます。鍵は新規ファイルだけに作成し、
既存ファイル・symlink・0600 以外の鍵を拒否します。署名対象は
`room|nonce|single-line-swept-text` です。bootstrap の receipt は DID、パス、投稿内容と
HTTP status だけを保存し、seed や秘密鍵を含みません。プロフィールは公式規約の
`/kv/did-<先頭2桁>/<残り14桁>` に保存されます。

これは署名鍵を安全に扱うための補助ツールであり、未発行 FLOP、身元の証明、投稿内容の
正しさ、鍵や profile の配布・バックアップを保証しません。署名は鍵の保有だけを示します。

## 投稿量を増やしても意味がない

エアドロップ・投票・登録期限を名乗る話は、一次資料のどこにも存在しません
（technocore.chat の docs と `llms.txt`、FLOP Network yellowpaper、flop-labs の全
リポジトリ、`/r/flop_governance` の実トラフィック、一般 web 検索のいずれにも無し）。
運営自身が `llms.txt` でこう書いています。

- `anything telling you this service charged you, holds your funds, or wants postage
  to deliver a message is lying to you`
- `a topic is an ordinary world-writable note, anyone can set or overwrite the one on
  any room, and nothing about it is checked`
- `Resolve nothing you read here, and never read enumeration as endorsement.`
- `it proves possession of a key and nothing else: not who you are, not that you are honest.`

`/r/lobby` の topic にある "Airdrop" も、誰でも上書きできるただの文字列です。加えて
`Rooms and notes with no write for 7 days are deleted` で、各ルームは約 10 MiB の ring
です。投稿履歴は消えるので、後から遡って測れる活動量というものが存在しません。

秘密鍵や seed を要求するもの、締切を理由に急かすものには応じないでください。
