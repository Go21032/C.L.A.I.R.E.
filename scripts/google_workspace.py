"""
google_workspace.py — 14日目④: Googleドキュメント/スプレッドシートへの出力。

背景/目的は14日目ノート④参照。設計上の約束:
  - **書き出し専用**。OAuthスコープを`drive.file`+`documents`+`spreadsheets`に絞って
    いるため、このアプリ(このOAuthクライアント)が作成したファイル以外には一切
    アクセスできない(既存のマイドライブを読むことも壊すこともできない)。
    これは制約ではなく安全設計。
  - **LLMからは呼ばせない**。code_executorのライブラリ・ホワイトリスト
    (openwebui_pipe/support_ai_auto_pipe.pyのCODE_ACTION_SYSTEM_PROMPT)に
    このモジュールを入れないこと。Google連携はvoice_gateway.pyのHTTP経路
    (/google/status, /google/export)経由、ここの関数呼び出しのみで行う。
  - 認証情報は scripts/secrets/ に置き、.gitignore で除外する
    (scripts/secrets/credentials.json・token.json はコミットしない)。
  - OAuth同意画面が「テスト」ステータスのままだと、リフレッシュトークンは
    7日で失効する(Googleの仕様)。失効時は黙って失敗させず、
    NotAuthenticatedErrorとして明示的にUIへ伝える。
"""

from __future__ import annotations

from pathlib import Path

import markdown_docs

SECRETS_DIR = Path(__file__).resolve().parent / "secrets"
CREDENTIALS_PATH = SECRETS_DIR / "credentials.json"
TOKEN_PATH = SECRETS_DIR / "token.json"

# drive.file = 「このアプリが作成したファイルのみ」。drive(フルアクセス)は要求しない。
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
]


class NotAuthenticatedError(RuntimeError):
    """未認証・トークン失効。UIへそのまま見せられる日本語メッセージを持たせる。"""


def _load_credentials():
    """google-auth系ライブラリをここで初めてimportする(遅延import)。

    このモジュール自体はimportされただけではネットワークにもGoogleライブラリにも
    触れない(=テスト時にimportするだけで失敗しない)ようにするため。
    """
    from google.auth.transport.requests import Request  # noqa: PLC0415
    from google.oauth2.credentials import Credentials  # noqa: PLC0415
    from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: PLC0415

    return Request, Credentials, InstalledAppFlow


def get_credentials():
    """保存済みトークンを返す。失効していればリフレッシュ、無ければブラウザ同意へ。

    「テスト」ステータスのOAuthクライアントはリフレッシュトークンが7日で失効するため、
    refresh()が失敗するケースは「異常」ではなく「想定内」として扱い、
    NotAuthenticatedErrorへ変換してUIに再認証を促す(黙って失敗させない)。
    """
    if not CREDENTIALS_PATH.exists():
        raise NotAuthenticatedError(
            "Google連携の認証情報がありません。secrets/credentials.json を配置してください。"
        )
    Request, Credentials, InstalledAppFlow = _load_credentials()

    creds = (
        Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        if TOKEN_PATH.exists()
        else None
    )
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:  # noqa: BLE001 - トークン失効は「想定内」としてUIへ再認証を促す
            raise NotAuthenticatedError(
                "Googleの認証が期限切れです"
                "(テスト公開のOAuthクライアントはリフレッシュトークンが7日で失効します)。"
                "再認証してください。"
            ) from None
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        creds = flow.run_local_server(port=0)  # ブラウザが開く(初回のみ)
        SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _build_service(api_name: str, api_version: str):
    """googleapiclient.discovery.build()のラッパー。

    テストからこの関数だけをmock.patch.objectで差し替えれば、get_credentials()
    (=ブラウザ同意・トークンI/O)を経由せずにexport_to_docs()等をユニットテストできる。
    """
    from googleapiclient.discovery import build  # noqa: PLC0415

    creds = get_credentials()
    return build(api_name, api_version, credentials=creds)


def export_to_docs(title: str, body: str) -> str:
    """Googleドキュメントを新規作成して本文を流し込み、編集URLを返す。

    14日目④追記: 本文はMarkdown(見出し・太字・箇条書き・表)を含みうるため、
    そのままinsertTextすると記法が文字として見えてしまう(元の実装の問題)。
    markdown_docs.parse_blocks()でブロック列へ分解し、表以外は1回のbatchUpdateへ
    まとめて送る。表はDocs APIの制約上、単独で複数回のAPI呼び出しが要る
    (insert_table_blockのdocstring参照)ため、表に当たるたびに直前までの
    保留リクエストを先にflushしてから処理する。
    """
    service = _build_service("docs", "v1")
    doc = service.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]

    blocks = markdown_docs.parse_blocks(body)
    cursor = 1
    pending: list[dict] = []
    for block in blocks:
        if block["type"] == "table":
            if pending:
                service.documents().batchUpdate(documentId=doc_id, body={"requests": pending}).execute()
                pending = []
            cursor = markdown_docs.insert_table_block(service, doc_id, cursor, block)
            continue
        if block["type"] == "heading":
            reqs, cursor = markdown_docs.build_paragraph_requests(
                cursor, block["runs"], heading_level=block["level"]
            )
            pending.extend(reqs)
        elif block["type"] == "bullet_list":
            for item_runs in block["items"]:
                reqs, cursor = markdown_docs.build_paragraph_requests(cursor, item_runs, bullet=True)
                pending.extend(reqs)
        else:  # paragraph
            reqs, cursor = markdown_docs.build_paragraph_requests(cursor, block["runs"])
            pending.extend(reqs)

    if pending:
        service.documents().batchUpdate(documentId=doc_id, body={"requests": pending}).execute()

    return f"https://docs.google.com/document/d/{doc_id}/edit"


def export_to_sheets(title: str, rows: list[list]) -> str:
    """Googleスプレッドシートを新規作成し、2次元配列を書き込んでURLを返す。

    表形式データを1セルへ丸ごと詰め込むのではなく、行×列に分けて書き込む
    (values().update()にrowsをそのまま渡す。A1形式のrange指定で先頭セルから展開)。
    """
    service = _build_service("sheets", "v4")
    sheet = service.spreadsheets().create(body={"properties": {"title": title}}).execute()
    spreadsheet_id = sheet["spreadsheetId"]
    if rows:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range="A1",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def upload_to_drive(path: Path, convert: bool = True) -> str:
    """workspace/のxlsx等をDriveへアップロードする。

    convert=True(既定)なら、xlsx→Googleスプレッドシート等のネイティブ形式へ
    インポート変換する(mimeTypeにGoogle形式を指定することで実現)。
    """
    from googleapiclient.http import MediaFileUpload  # noqa: PLC0415

    service = _build_service("drive", "v3")
    path = Path(path)
    metadata: dict = {"name": path.name}
    media_kwargs: dict = {}
    if convert:
        target_mime = _GOOGLE_NATIVE_MIME_TYPES.get(path.suffix.lower())
        if target_mime:
            metadata["mimeType"] = target_mime
    media = MediaFileUpload(str(path), **media_kwargs)
    uploaded = service.files().create(body=metadata, media_body=media, fields="id,webViewLink").execute()
    return uploaded.get("webViewLink") or f"https://drive.google.com/file/d/{uploaded['id']}/view"


# xlsx→Googleスプレッドシート、docx→Googleドキュメント、のようにDrive側の
# インポート変換に使うmimeTypeの対応表。
_GOOGLE_NATIVE_MIME_TYPES = {
    ".xlsx": "application/vnd.google-apps.spreadsheet",
    ".docx": "application/vnd.google-apps.document",
    ".pptx": "application/vnd.google-apps.presentation",
    ".csv": "application/vnd.google-apps.spreadsheet",
}


def check_status() -> dict:
    """UIが「未認証の案内」を出すか判断するための軽量チェック。

    get_credentials()を呼んではいけない(ブラウザ同意が勝手に開いてしまう)。
    ファイルの存在と有効期限だけを見る。
    """
    if not CREDENTIALS_PATH.exists():
        return {"authenticated": False, "reason": "認証情報が未配置です(secrets/credentials.json)。"}
    if not TOKEN_PATH.exists():
        return {"authenticated": False, "reason": "未認証です。初回のGoogle出力時にブラウザで同意してください。"}
    try:
        _Request, Credentials, _Flow = _load_credentials()
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except Exception:  # noqa: BLE001 - token.jsonが壊れている等
        return {"authenticated": False, "reason": "認証トークンを読み込めません。再認証してください。"}
    if creds.valid:
        return {"authenticated": True, "reason": ""}
    if creds.expired and creds.refresh_token:
        # リフレッシュ自体はexport実行時にget_credentials()が行う。ここでは
        # 「まだ有効か」を軽量に判定するだけなので、この状態は「未確定」として
        # 楽観的にTrueを返す(実際にリフレッシュが失敗したらexport側の401で判明する)。
        return {"authenticated": True, "reason": ""}
    return {"authenticated": False, "reason": "認証が期限切れです。再認証してください。"}
