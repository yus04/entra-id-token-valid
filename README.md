# entra-id-token-valid

Entra ID への直接認証と、External ID 経由のフェデレーション認証を選び、リダイレクト先で受け取ったトークンを表示するだけの、ローカル動作確認用 Python Web アプリです。

Flask と Microsoft 公式の MSAL を使用し、認可コードフローでトークンを取得します。データベースやフロントエンドのビルドは不要です。

| 認証ボタン | 認証経路 | Web アプリが使うアプリ登録 |
| --- | --- | --- |
| Entra ID で認証 | Web アプリ → 社内 Entra ID → Web アプリ | 社内の従業員用テナント |
| External ID で認証 | Web アプリ → External ID のユーザーフロー → 社内 Entra ID → External ID → Web アプリ | External ID の顧客テナント |

片方だけの設定でも起動できます。未設定側のボタンは無効になり、別の認証先へ自動的に切り替えることはありません。各認証先は必要な項目をすべて設定してください。設定途中の項目がある場合や、両方とも未設定の場合は起動時にエラーにします。

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

`.env` を編集し、署名鍵とアプリ登録の値を設定します。`FLASK_SECRET_KEY` と `ENTRA_REDIRECT_URI` は両方の認証で共通の必須設定です。未設定なら起動時にエラーにします。`ENTRA_REDIRECT_URI` は名前にかかわらず、External ID 側の Web アプリ登録にも同じ値を登録してください。

```dotenv
FLASK_SECRET_KEY=上記で生成した署名鍵
ENTRA_REDIRECT_URI=http://localhost:5000/auth/callback
ENTRA_TENANT_ID=社内ディレクトリのテナントID
ENTRA_CLIENT_ID=社内テナントのWebアプリのクライアントID
ENTRA_CLIENT_SECRET=社内テナントのWebアプリのシークレットの値
ENTRA_AUTHORITY=
ENTRA_SCOPES=User.Read
EXTERNAL_ID_TENANT_ID=
EXTERNAL_ID_SUBDOMAIN=
EXTERNAL_ID_CLIENT_ID=
EXTERNAL_ID_CLIENT_SECRET=
EXTERNAL_ID_SCOPES=
```

```bash
python app.py
```

ブラウザーで **http://localhost:5000** を開き、使いたい認証経路のボタンを押してください。サインインと必要な同意の後、`http://localhost:5000/auth/callback` に戻り、認証経路名と以下を表示します。

- ID トークンの元の文字列
- MSAL で検証された ID トークンのクレーム（JSON）
- アクセストークンの元の文字列（返された場合）

アプリ自体は API を呼び出しません。アクセストークンは JWT とは限らないため、デコードや検証は行いません。

## Entra External ID を使用する場合

「External ID で認証」では、Web アプリが認証要求を送る相手と ID トークンの発行元は **External ID の顧客テナント** です。社内 Entra ID は、その先のフェデレーション先です。

1. External ID の顧客テナントで Web アプリを登録し、共通の `ENTRA_REDIRECT_URI` を **Web** リダイレクト URI に登録します。
2. 顧客テナントの **External Identities → ユーザーフロー** で、使用するフロー（例：`Federation-User-Flow`）を開き、**アプリケーション**にその Web アプリを関連付けます。
3. そのユーザーフローの ID プロバイダーとして、設定済みの社内 Entra ID（例：`Contoso Employee`）を有効にします。テナントに ID プロバイダーを登録しただけではなく、フローで使える状態になっていることを確認してください。
4. `.env` の `EXTERNAL_ID_*` に、顧客テナントの ID・サブドメイン・Web アプリ登録のクライアント ID・シークレットを設定します。**フェデレーション用に社内 Entra ID に登録した上流アプリのクライアント ID とは別です。**
5. 「External ID で認証」を押し、External ID のサインイン画面で社内 Entra ID の ID プロバイダーを選択します。

ユーザーフローは **External ID 側のアプリ登録との関連付け**で決まります。Azure AD B2C のような `p=...` やポリシー名を認証 URL に追加する構成ではありません。Web アプリから実際のユーザーフロー／フェデレーション設定を作成・変更することはありません。

Codespaces で External ID だけを使用する場合の `.env` は次の形です。両方のボタンを使う場合は、空欄の `ENTRA_*` に社内テナントの Web アプリ登録を設定してください。

```dotenv
FLASK_SECRET_KEY=<一度生成して保持する十分長いランダムな署名鍵>
ENTRA_REDIRECT_URI=https://<転送先ホスト名>/auth/callback
ENTRA_TENANT_ID=
ENTRA_CLIENT_ID=
ENTRA_CLIENT_SECRET=
ENTRA_AUTHORITY=
ENTRA_SCOPES=User.Read
EXTERNAL_ID_TENANT_ID=<External ID のテナント ID>
EXTERNAL_ID_SUBDOMAIN=<External ID のサブドメインのみ>
EXTERNAL_ID_CLIENT_ID=<External ID 側のWebアプリのクライアント ID>
EXTERNAL_ID_CLIENT_SECRET=<External ID 側のWebアプリのシークレットの値>
EXTERNAL_ID_SCOPES=
```

External ID の Authority は必ず `https://<EXTERNAL_ID_SUBDOMAIN>.ciamlogin.com/<EXTERNAL_ID_TENANT_ID>` として組み立てます。`EXTERNAL_ID_SUBDOMAIN` に `https://` や `.ciamlogin.com` は含めないでください。`login.microsoftonline.com` へフォールバックしません。CIAM に対応した MSAL がエンドポイントの検出・トークンの検証を行い、検証を無効化する設定は使用しません。

一方、直接認証の Authority は既定で `https://login.microsoftonline.com/<ENTRA_TENANT_ID>` です。`ENTRA_AUTHORITY` は従業員用の `login.microsoftonline.com` の HTTPS Authority を指定する場合に限り使用できます。以前の設定で **`ENTRA_AUTHORITY` に CIAM URL を入れていた場合は空にし、外部テナントの値を `EXTERNAL_ID_*` に移してください**。設定の取り違えを防ぐため、直接認証に CIAM URL を設定すると起動時にエラーにします。

External ID でまず ID トークンと `roles` だけを確認する場合は、**`EXTERNAL_ID_SCOPES=` を空にしてください（既定も空）**。MSAL が `openid`・`profile`・`offline_access` を自動追加するため、これらを手動で書く必要はありません。Microsoft Graph の `User.Read` を要求せずに認証を開始でき、アクセストークンが返らない場合も ID トークンを表示します。独自 API のアクセストークンも必要な場合は、External ID 側で公開・許可したスコープを空白区切りで指定します。`ENTRA_SCOPES` は直接認証専用で、既定は `User.Read` です。

`roles` や `name` は、発行された ID トークンに含まれる場合にクレーム欄へ表示されます。アプリ側で補完・生成はしません。ロールの定義・割り当てやユーザーフローの出力クレームを External ID 側で設定してください。このアプリは表示専用で、ロールに基づくアクセス制御は行いません。

## Codespaces などのポート転送を使う場合

ブラウザーが別の PC 上で動いている場合、`localhost` は Python を実行している環境ではなく、ブラウザー側の PC を指します。転送 URL でアプリを開く場合は、コールバックも同じ転送先に合わせる必要があります。

1. 転送ポートは **非公開** のまま、ポート一覧の「ブラウザーで開く」でアプリを通常のブラウザータブに開きます。埋め込みプレビューは使わず、転送サービスの GitHub サインインが表示された場合は先に完了してください。
2. そのタブのアプリの URL が `https://<転送先ホスト名>` の場合、`.env` に `ENTRA_REDIRECT_URI=https://<転送先ホスト名>/auth/callback` を設定します。`localhost` の設定を残さないでください。HTTPS を指定するとセッション Cookie の `Secure` が有効になります（ローカル HTTP の場合のみ無効）。リクエストヘッダーではなく設定値を基準にするため、HTTPS を終端するポート転送でも動作します。
3. Entra ID のアプリ登録の **Web** リダイレクト URI にも、同じ HTTPS URL を登録します。
4. Python サーバーを再起動し、設定したホスト名のタブから使いたい経路の認証ボタンを押します。

認証ボタンはフォーム送信ではなく、トップレベルのリンク遷移です。フォームのリダイレクトが `form-action 'self'` に遮断されたり、埋め込みフレームのままサインイン画面を開いたりするのを避けています。

## 認証セッションの保持

認証ボタンは `ENTRA_REDIRECT_URI` と同じオリジンの `/login/entra` または `/login/external` を開き、そこで認証を開始します。別のホスト名や HTTP でトップページを開いても、Cookie を保存する前に設定済み URL の `/login/<認証先>/start` に移動します。リバースプロキシの Host・転送ヘッダーに依存して URL を推測しません。旧 `/login`・`/login/start` は直接認証用として残しています。

Entra ID に移動する前に `/login/continue` で認証用 Cookie が戻ってくるか確認します。保存できていない場合は、この段階で停止して原因を表示するため、サインイン後に初めてセッション消失に気付くことを避けられます。Cookie 名は `entra_auth_<設定ごとの識別子>` で、ほかの Flask アプリの汎用的な `session` Cookie との衝突も避けます。

外部サイトから戻った最初のコールバックで Cookie が送信されない場合は、Cookie を削除せずに確認用ページを表示し、同じサイト内で一度だけ再アクセスします。JavaScript が無効な場合は「認証結果を表示」を押してください。**再アクセス後も元の Cookie がなければ認証は受け入れません。** Cookie 内の認証情報と `state` の一致を確認し、MSAL による `nonce`・PKCE・トークン検証も維持しています。

認証先も署名付き Cookie に保存し、コールバックでは開始時と同じ Authority・クライアント ID・シークレットでトークンを取得します。クエリパラメーターで別の認証先へ切り替えることはできません。同じブラウザーで別の認証を開始すると前の認証は無効になるため、二つの経路は一つずつ確認してください。

再確認でも失敗した場合、画面とサーバーログで次を区別できます。ログには Cookie の値、認可コード、トークンは追加出力しません。

| ログの reason | 確認箇所 |
| --- | --- |
| `cookie_not_sent` | アプリのボタンから開始したか、ブラウザーの Cookie 制限、設定 URL・HTTPS が正しいか |
| `invalid_cookie` | 署名鍵の変更・不一致、古い／壊れた Cookie |
| `flow_not_found` | 処理済みの結果ページの再読み込み、認証途中の別タブ操作 |

この変更前に開始した認証は引き継げません。サーバーを再起動後、古い結果ページを再読み込みせず、最初の画面の認証ボタンから開始してください。Entra 管理画面の「ユーザーフローの実行」だけを直接使っても、アプリ側の認証セッションは作成されません。

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

テストは Entra ID との通信をモックし、認証開始、コールバック、エラー表示、トークンを Cookie に残さないことに加え、プロセス再作成後の Cookie 復元、HTTPS の Secure Cookie、認証前の Cookie 確認、初回コールバックで Cookie が送られない場合の復旧、不正な `state` で認証情報を消費しないことを確認します。また、二つのボタン、Authority・資格情報・スコープの使い分け、コールバックでの認証先の保持、未設定側を別経路にフォールバックしないことも確認します。実際のサインインには、ご自身のテナント・アプリ登録・アカウントでの確認が必要です。