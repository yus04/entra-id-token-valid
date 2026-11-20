# entra-id-token-valid

Entra ID のサインイン画面で認証し、リダイレクト先で受け取ったトークンを表示するだけの、ローカル動作確認用 Python Web アプリです。

Flask と Microsoft 公式の MSAL を使用し、認可コードフローでトークンを取得します。データベースやフロントエンドのビルドは不要です。

## 1. Entra ID のアプリ登録

Microsoft Entra 管理センターで以下を設定します。アプリ登録や同意には、テナントのポリシーに応じた権限が必要です。

1. **アプリの登録 → 新規登録**でアプリを作成します。サポートされるアカウントの種類は「この組織ディレクトリのみに含まれるアカウント」を選択します。
2. **認証 → プラットフォームの追加 → Web** に、リダイレクト URI として `http://localhost:5000/auth/callback` を登録します。SPA ではなく **Web** を選択してください。暗黙的な許可のチェックは不要です。
3. **概要**に表示される「ディレクトリ（テナント）ID」と「アプリケーション（クライアント）ID」を控えます。
4. **証明書とシークレット → 新しいクライアント シークレット**でシークレットを作成し、作成直後に表示される **値** を控えます。シークレット ID ではありません。
5. 通常の従業員用テナントで Microsoft Graph のアクセストークンも取得する場合は、**API のアクセス許可**で Microsoft Graph の **委任されたアクセス許可 `User.Read`** を確認・追加します。テナントでユーザーによる同意が制限されている場合は、管理者による同意が必要です。External ID は後述の設定を使用してください。

## 2. ローカル起動

Python 3.10 以上を使用してください。認証のためのインターネット接続が必要です。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell では、仮想環境の有効化に `.venv\Scripts\Activate.ps1`、ファイルのコピーに `Copy-Item .env.example .env` を使用します。

まず以下でセッション署名鍵を一度だけ生成します。出力は秘密情報として扱い、`.env` の `FLASK_SECRET_KEY` に設定してください。起動のたびに生成し直さず、再起動後も同じ値を使用します。

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

`.env` を編集し、署名鍵とアプリ登録の値を設定します。`FLASK_SECRET_KEY` と `ENTRA_REDIRECT_URI` は必須です。未設定なら起動時にエラーにします。

```dotenv
FLASK_SECRET_KEY=上記で生成した署名鍵
ENTRA_TENANT_ID=ディレクトリのテナントID
ENTRA_CLIENT_ID=アプリケーションのクライアントID
ENTRA_CLIENT_SECRET=クライアントシークレットの値
ENTRA_REDIRECT_URI=http://localhost:5000/auth/callback
ENTRA_AUTHORITY=
ENTRA_SCOPES=User.Read
```

```bash
python app.py
```

ブラウザーで **http://localhost:5000** を開き、「Entra ID で認証」を押してください。サインインと必要な同意の後、`http://localhost:5000/auth/callback` に戻り、以下を表示します。

- ID トークンの元の文字列
- MSAL で検証された ID トークンのクレーム（JSON）
- アクセストークンの元の文字列（返された場合）

アプリ自体は API を呼び出しません。アクセストークンは JWT とは限らないため、デコードや検証は行いません。

## Entra External ID を使用する場合

Web アプリが直接認証する相手は **External ID の顧客テナント** です。社内 Entra ID とのフェデレーションを使う場合でも、社内テナントの ID やアプリ登録ではなく、External ID 側のテナント・アプリ登録の値を設定してください。対象アプリを External ID のサインアップ／サインインのユーザーフローに関連付け、必要な外部 ID プロバイダーも設定します。

Codespaces で External ID にサインインする場合の `.env` は次の形です。プレースホルダーは実際の値に置き換えてください。

```dotenv
FLASK_SECRET_KEY=一度生成して保持する十分長いランダムな署名鍵
ENTRA_TENANT_ID=<External ID のテナント ID>
ENTRA_CLIENT_ID=<External ID 側のアプリケーション ID>
ENTRA_CLIENT_SECRET=<External ID 側のクライアントシークレットの値>
ENTRA_REDIRECT_URI=https://<転送先ホスト名>/auth/callback
ENTRA_AUTHORITY=https://<テナントのサブドメイン>.ciamlogin.com/<External ID のテナント ID>
ENTRA_SCOPES=
```

`ENTRA_AUTHORITY` には、そのテナントの OpenID Configuration に対応する Authority を指定します。上記は標準的な CIAM Authority の形式です。`/v2.0/.well-known/openid-configuration` や `/oauth2/v2.0/authorize` 自体の URL は指定しません。未設定・空の場合のみ、従来どおり `https://login.microsoftonline.com/<ENTRA_TENANT_ID>` を使用します。CIAM に対応した MSAL がエンドポイントの検出・トークンの検証を行い、検証を無効化する設定は使用しません。

External ID でまず ID トークンと `roles` だけを確認する場合は、**`ENTRA_SCOPES=` を空にしてください**。MSAL が `openid`・`profile`・`offline_access` を自動追加するため、これらを `ENTRA_SCOPES` に書く必要はありません。Microsoft Graph の `User.Read` を要求せずに認証を開始でき、アクセストークンが返らない場合も ID トークンを表示します。独自 API のアクセストークンも必要な場合は、External ID 側で公開・許可したスコープを空白区切りで指定します。`ENTRA_SCOPES` 自体が未設定の場合は、従来互換の `User.Read` を使用します。

`roles` や `name` は、発行された ID トークンに含まれる場合にクレーム欄へ表示されます。アプリ側で補完・生成はしません。ロールの定義・割り当てやユーザーフローの出力クレームを External ID 側で設定してください。このアプリは表示専用で、ロールに基づくアクセス制御は行いません。

## Codespaces などのポート転送を使う場合

ブラウザーが別の PC 上で動いている場合、`localhost` は Python を実行している環境ではなく、ブラウザー側の PC を指します。転送 URL でアプリを開く場合は、コールバックも同じ転送先に合わせる必要があります。

1. 転送ポートは **非公開** のまま、ポート一覧の「ブラウザーで開く」でアプリを通常のブラウザータブに開きます。埋め込みプレビューは使わず、転送サービスの GitHub サインインが表示された場合は先に完了してください。
2. そのタブのアプリの URL が `https://<転送先ホスト名>` の場合、`.env` に `ENTRA_REDIRECT_URI=https://<転送先ホスト名>/auth/callback` を設定します。`localhost` の設定を残さないでください。HTTPS を指定するとセッション Cookie の `Secure` が有効になります（ローカル HTTP の場合のみ無効）。リクエストヘッダーではなく設定値を基準にするため、HTTPS を終端するポート転送でも動作します。
3. Entra ID のアプリ登録の **Web** リダイレクト URI にも、同じ HTTPS URL を登録します。
4. Python サーバーを再起動し、設定したホスト名のタブから「Entra ID で認証」を押します。

認証ボタンはフォーム送信ではなく、トップレベルのリンク遷移です。フォームのリダイレクトが `form-action 'self'` に遮断されたり、埋め込みフレームのままサインイン画面を開いたりするのを避けています。

## 注意事項

- **トークンを画面表示するローカル確認専用アプリです。本番環境や公開サーバーには配置しないでください。** サーバーはループバックアドレス `127.0.0.1:5000` のみで待ち受け、デバッガーは無効です。
- `ENTRA_REDIRECT_URI=http://localhost:5000/auth/callback` の場合、ブラウザーでも `127.0.0.1` ではなく **`localhost`** を使用してください。ポート転送を使う場合も、認証開始時とコールバック時でホスト名が異なるとセッション Cookie を引き継げません。
- `.env` は Git の管理対象外です。シークレット、トークン、個人情報を含むクレームをコミット・共有しないでください。
- トークンをファイル、セッション Cookie、永続キャッシュには保存しません。認証中の `state`・`nonce`・PKCE 情報のみ署名付き Cookie に保持し、MSAL で認証応答を検証します。
- レスポンスには `Cache-Control: no-store` を設定していますが、表示中のブラウザーや拡張機能、画面共有などからの漏えいには注意してください。確認後はタブを閉じてください。リフレッシュトークンは表示しません。
- コールバックは一度だけ処理します。結果ページの再読み込みや別タブでの認証開始後には、最初の画面から認証し直してください。`FLASK_SECRET_KEY` を保持すれば、プロセスを再起動しても認証中の Cookie を復元できます。複数プロセスで実行する場合も同じ署名鍵・認証設定を使用してください。
- 署名鍵を変更した場合は既存 Cookie が無効になります。「認証セッションがありません」が続く場合は、署名鍵の一致、開始時とコールバック時のホスト名、HTTPS 設定、ブラウザーの Cookie 制限を確認し、古いタブを閉じて最初から認証してください。署名鍵の固定だけで Cookie の未送信まで解消するわけではありません。
- `AADSTS50011` が出る場合はリダイレクト URI の完全一致を、クライアントシークレットのエラーが出る場合は「値」と有効期限を確認してください。

## テスト

```bash
python -m unittest discover -s tests -v
```

テストは Entra ID との通信をモックし、認証開始、コールバック、エラー表示、トークンを Cookie に残さないことに加え、プロセス再作成後の Cookie 復元、HTTPS の Secure Cookie、CIAM Authority、追加スコープなしの認証を確認します。実際のサインインには、ご自身のテナント・アプリ登録・アカウントでの確認が必要です。