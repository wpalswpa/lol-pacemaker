"""AGENTS §22: single source of the in-site help center (category › subcategory › article, 5 languages).

`python scripts/help_content.py` rewrites the `const HELP = …;` block in docs/index.html and web/index.html.
Every string is rendered with textContent. Only facts of this app; images are real captures only.
Fields per article: t title, s one-line answer, st steps (list), n note (optional), img (optional), link (optional).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANGS = ("en", "ko", "ja", "es", "pt")


def L(en, ko, ja, es, pt):
    return {"en": en, "ko": ko, "ja": ja, "es": es, "pt": pt}


def A(id, t, s, st, n=None, img=None, link=None):
    a = {"id": id, "t": t, "s": s, "st": st}
    if n:
        a["n"] = n
    if img:
        a["img"] = img
    if link:
        a["link"] = link
    return a


HELP = [
    {"id": "start", "t": L("Install & get started", "설치·시작하기", "インストールと開始", "Instalación e inicio", "Instalação e início"), "subs": [
        {"id": "install", "t": L("Download & install", "다운로드·설치", "ダウンロードとインストール", "Descarga e instalación", "Download e instalação"), "items": [
            A("win-install",
              L("Install on Windows", "Windows에 설치하기", "Windowsにインストール", "Instalar en Windows", "Instalar no Windows"),
              L("One installer sets everything up, including auto-start at sign-in.", "설치 파일 하나로 설치하고, 로그인할 때 자동으로 실행되게 맞춥니다.", "インストーラー1つで、サインイン時の自動起動まで設定します。", "Un instalador lo configura todo, incluido el inicio automático.", "Um instalador configura tudo, incluindo o início automático."),
              L(["Click [Download app] at the top right and download LoLPacemaker-Setup.exe from the Windows card.", "If a SmartScreen warning appears, click [More info → Run anyway].", "Keep 'Start automatically with Windows (auto-popup when LoL match starts)' checked and finish the installer."],
                ["오른쪽 위 [앱 다운로드]를 누르고 Windows 카드에서 LoLPacemaker-Setup.exe를 받습니다.", "보안 경고(SmartScreen) 창이 뜨면 [추가 정보 → 실행]을 누릅니다.", "설치 마법사(영어)의 'Start automatically with Windows (auto-popup when LoL match starts)'를 켠 채로 설치를 마칩니다."],
                ["右上の［アプリをダウンロード］からWindowsカードのLoLPacemaker-Setup.exeを入手します。", "SmartScreenの警告が出たら［詳細情報 → 実行］を押します。", "インストーラー（英語）の'Start automatically with Windows (auto-popup when LoL match starts)'をオンのまま終えます。"],
                ["Pulsa [Descargar app] arriba a la derecha y descarga LoLPacemaker-Setup.exe desde la tarjeta de Windows.", "Si aparece el aviso de SmartScreen, pulsa [Más información → Ejecutar de todas formas].", "Deja marcado 'Start automatically with Windows (auto-popup when LoL match starts)' (instalador en inglés) y termina."],
                ["Clique em [Baixar app] no canto superior direito e baixe LoLPacemaker-Setup.exe no cartão do Windows.", "Se aparecer o aviso do SmartScreen, clique em [Mais informações → Executar assim mesmo].", "Mantenha 'Start automatically with Windows (auto-popup when LoL match starts)' (instalador em inglês) marcado e conclua."]),
              L("The warning appears because this free open-source app has no paid code-signing certificate.", "유료 코드 서명 인증서가 없는 무료 오픈소스라 경고가 뜹니다.", "有料のコード署名証明書がない無料オープンソースのため警告が出ます。", "El aviso aparece porque esta app libre no tiene certificado de firma de pago.", "O aviso aparece porque este app gratuito não tem certificado de assinatura pago.")),
            A("mac-install",
              L("Install on macOS", "macOS에 설치하기", "macOSにインストール", "Instalar en macOS", "Instalar no macOS"),
              L("Unzip the app and open it; allow it once if macOS blocks it.", "앱 압축을 풀고 엽니다. 맥이 막으면 한 번만 허용합니다.", "アプリを解凍して開きます。macOSがブロックしたら一度だけ許可します。", "Descomprime la app y ábrela; permítela una vez si macOS la bloquea.", "Descompacte o app e abra; permita uma vez se o macOS bloquear."),
              L(["Download LoLPacemaker-macos.zip from the macOS card (Apple Silicon) and unzip it.", "Open the app.", "If it is blocked, go to [System Settings → Privacy & Security], scroll down and click [Open Anyway] next to LoLPacemaker."],
                ["macOS 카드에서 LoLPacemaker-macos.zip(Apple Silicon)을 받아 압축을 풉니다.", "앱을 엽니다.", "실행이 막히면 [시스템 설정 → 개인정보 보호 및 보안] 맨 아래에서 LoLPacemaker 옆 [그래도 열기]를 누릅니다."],
                ["macOSカードからLoLPacemaker-macos.zip（Apple Silicon）を入手して解凍します。", "アプリを開きます。", "ブロックされたら［システム設定 → プライバシーとセキュリティ］の一番下でLoLPacemakerの［このまま開く］を押します。"],
                ["Descarga LoLPacemaker-macos.zip (Apple Silicon) desde la tarjeta de macOS y descomprímelo.", "Abre la app.", "Si se bloquea, ve a [Ajustes del Sistema → Privacidad y seguridad] y pulsa [Abrir igualmente] junto a LoLPacemaker."],
                ["Baixe LoLPacemaker-macos.zip (Apple Silicon) no cartão do macOS e descompacte.", "Abra o app.", "Se for bloqueado, vá em [Ajustes do Sistema → Privacidade e Segurança] e clique em [Abrir Mesmo Assim] ao lado de LoLPacemaker."]),
              L("The button name can differ between macOS versions.", "단추 이름은 macOS 버전마다 조금 다를 수 있습니다.", "ボタン名はmacOSのバージョンで異なる場合があります。", "El nombre del botón puede variar según la versión de macOS.", "O nome do botão pode variar conforme a versão do macOS.")),
            A("checksum",
              L("Check that the download is the original", "받은 파일이 원본인지 확인하기", "ダウンロードしたファイルが本物か確認", "Comprobar que la descarga es la original", "Conferir se o download é o original"),
              L("Compare the file's SHA-256 with the published checksums.", "파일의 SHA-256 값을 공개된 체크섬과 비교합니다.", "ファイルのSHA-256を公開チェックサムと比べます。", "Compara el SHA-256 del archivo con las sumas publicadas.", "Compare o SHA-256 do arquivo com as somas publicadas."),
              L(["Download checksums.sha256 from the same release ([Checksums] on this page).", "Windows: in PowerShell run Get-FileHash .\\LoLPacemaker-Setup.exe -Algorithm SHA256", "macOS: in Terminal run shasum -a 256 LoLPacemaker-macos.zip", "If both values match, the file is the original."],
                ["같은 릴리스의 checksums.sha256을 받습니다(이 화면의 [체크섬]).", "Windows: PowerShell에서 Get-FileHash .\\LoLPacemaker-Setup.exe -Algorithm SHA256 을 실행합니다.", "macOS: 터미널에서 shasum -a 256 LoLPacemaker-macos.zip 을 실행합니다.", "두 값이 같으면 원본입니다."],
                ["同じリリースのchecksums.sha256を入手します（このページの［チェックサム］）。", "Windows：PowerShellで Get-FileHash .\\LoLPacemaker-Setup.exe -Algorithm SHA256 を実行します。", "macOS：ターミナルで shasum -a 256 LoLPacemaker-macos.zip を実行します。", "値が一致すれば本物です。"],
                ["Descarga checksums.sha256 de la misma versión ([Checksums] en esta página).", "Windows: en PowerShell ejecuta Get-FileHash .\\LoLPacemaker-Setup.exe -Algorithm SHA256", "macOS: en Terminal ejecuta shasum -a 256 LoLPacemaker-macos.zip", "Si los valores coinciden, el archivo es el original."],
                ["Baixe checksums.sha256 da mesma versão ([Checksums] nesta página).", "Windows: no PowerShell execute Get-FileHash .\\LoLPacemaker-Setup.exe -Algorithm SHA256", "macOS: no Terminal execute shasum -a 256 LoLPacemaker-macos.zip", "Se os valores forem iguais, o arquivo é o original."])),
        ]},
        {"id": "first", "t": L("First launch", "첫 실행", "初回起動", "Primer inicio", "Primeira execução"), "items": [
            A("consent",
              L("The consent window on first launch", "처음 실행할 때 뜨는 동의 창", "初回起動時の同意ウィンドウ", "La ventana de consentimiento al iniciar", "A janela de consentimento na primeira execução"),
              L("It appears once. Nothing about your matches leaves your PC unless you opt in.", "한 번만 뜹니다. 동의하지 않으면 경기 정보는 PC 밖으로 나가지 않습니다.", "一度だけ表示されます。同意しない限り試合情報はPCの外に出ません。", "Aparece una vez. Nada de tus partidas sale del PC si no aceptas.", "Aparece uma vez. Nada das suas partidas sai do PC sem sua permissão."),
              L(["Read the notice: the app only reads Riot's official local API on this PC.", "'Share anonymous match numbers to improve the model (optional)' is off by default. Turn it on only if you want to help.", "Check 'Start with my computer and pop up automatically when a match starts' (on by default) and click [Start]."],
                ["안내를 확인합니다: 앱은 이 PC의 Riot 공식 로컬 API만 읽습니다.", "'모델 개선을 위해 익명 경기 수치 제공 (선택)'은 기본 꺼짐입니다. 돕고 싶을 때만 켭니다.", "'컴퓨터를 켜면 자동 실행하고, 경기가 시작되면 자동으로 띄우기'(기본 켬)를 확인하고 [시작]을 누릅니다."],
                ["案内を確認します：アプリはこのPCのRiot公式ローカルAPIだけを読みます。", "「モデル改善のため匿名の試合数値を提供する（任意）」は初期設定でオフです。協力したい場合だけオンにします。", "「PC起動時に自動実行し、試合開始時に自動表示する」（初期設定オン）を確認して［開始］を押します。"],
                ["Lee el aviso: la app solo lee la API local oficial de Riot en este PC.", "'Compartir números anónimos de partidas para mejorar el modelo (opcional)' está desactivado por defecto. Actívalo solo si quieres ayudar.", "Revisa 'Iniciar con el ordenador y mostrarse automáticamente al empezar una partida' (activado) y pulsa [Empezar]."],
                ["Leia o aviso: o app só lê a API local oficial da Riot neste PC.", "'Compartilhar números anônimos de partidas para melhorar o modelo (opcional)' vem desligado. Ligue só se quiser ajudar.", "Confira 'Iniciar com o computador e aparecer automaticamente quando a partida começar' (ligado) e clique em [Começar]."]),
              img="images/app_consent.png"),
            A("autostart",
              L("Turn auto-start on or off", "컴퓨터 켤 때 자동 실행 켜고 끄기", "自動起動のオン・オフ", "Activar o desactivar el inicio automático", "Ligar ou desligar o início automático"),
              L("With auto-start on, the HUD appears when a match starts and hides when it ends.", "켜 두면 경기가 시작될 때 HUD가 뜨고 끝나면 숨습니다.", "オンにすると試合開始でHUDが表示され、終了で隠れます。", "Activado, el HUD aparece al empezar la partida y se oculta al terminar.", "Ligado, o HUD aparece quando a partida começa e some quando termina."),
              L(["During a match, click the PACE MAKER logo at the top left of the HUD.", "Click 'Start with computer' in the menu (✓ means on)."],
                ["경기 중 HUD 왼쪽 위 PACE MAKER 로고를 누릅니다.", "메뉴에서 '컴퓨터 켤 때 자동 실행'을 누릅니다(✓ 표시가 켜짐)."],
                ["試合中、HUD左上のPACE MAKERロゴを押します。", "メニューの「PC起動時に自動実行」を押します（✓でオン）。"],
                ["Durante la partida, pulsa el logo PACE MAKER arriba a la izquierda del HUD.", "Pulsa 'Iniciar con el ordenador' en el menú (✓ = activado)."],
                ["Durante a partida, clique no logo PACE MAKER no canto superior esquerdo do HUD.", "Clique em 'Iniciar com o computador' no menu (✓ = ligado)."]),
              img="images/help_hud_menu.png"),
            A("language",
              L("Change the language", "언어 바꾸기", "言語を変更", "Cambiar el idioma", "Mudar o idioma"),
              L("Both the website and the in-game HUD support 5 languages (EN · KO · JA · ES · PT) independently, and your choice is saved automatically.", "웹사이트와 인게임 HUD 모두 5개 언어(영어·한국어·일본어·스페인어·포르투갈어)를 지원하며, 각각 독립적으로 설정되고 자동 저장됩니다.", "Webサイトとゲーム内HUDのどちらも5言語（EN・KO・JA・ES・PT）に対応しており、個別に設定・自動保存されます。", "Tanto la web como el HUD del juego admiten 5 idiomas (EN · KO · JA · ES · PT) de forma independiente y guardan tu elección.", "Tanto o site quanto o HUD no jogo suportam 5 idiomas (EN · KO · JA · ES · PT) separadamente e salvam sua escolha."),
              L(["Website: click the language selector at the top right (e.g. EN · Global) and choose your language. Feature previews, intro video, Help Center, and AI Analysis all switch immediately.", "In-game HUD: during a match, click the orange PACE MAKER logo at the top left of the HUD.", "Click 'Language' in the menu (each click cycles EN → KO → JA → ES → PT) — or click the language badge (e.g. EN / KO) directly on the win-rate bar.", "Your choice is saved to your PC and remembered next time you launch LoL."],
                ["웹사이트: 화면 오른쪽 위 언어 선택 상자(예: KO · 한국어)를 눌러 원하는 언어를 고릅니다. 핵심 기능 화면, 소개 영상, 도움말, AI 분석까지 전부 즉시 바뀝니다.", "인게임 HUD: 경기 중 HUD 왼쪽 위 주황색 PACE MAKER 로고를 누릅니다.", "메뉴에서 '언어: KO' 항목을 누를 때마다 5개 언어(EN → KO → JA → ES → PT)가 차례로 바뀌며, 승률 바 오른쪽의 언어 버튼(예: KO)을 눌러도 바로 바꿀 수 있습니다.", "선택한 언어는 PC에 자동 저장되어 다음 경기부터 그대로 유지됩니다."],
                ["Webサイト：画面右上の言語セレクター（例：JA・日本語）から言語を選ぶと、機能プレビュー・紹介動画・ヘルプ・AI分析まで即座に切り替わります。", "ゲーム内HUD：試合中にHUD左上のオレンジ色のPACE MAKERロゴをクリックします。", "メニューの「言語」を押すたびに5言語（EN→KO→JA→ES→PT）が順に切り替わります。勝率バー右端の言語ボタンからも直接切り替え可能です。", "選択した言語はPCに自動保存され、次回起動時も維持されます。"],
                ["Web: haz clic en el selector de idioma arriba a la derecha (p. ej., ES · Español). Las vistas previas, el vídeo, la ayuda y el Análisis IA cambian al instante.", "HUD en partida: haz clic en el logo naranja PACE MAKER arriba a la izquierda.", "Haz clic en 'Idioma' en el menú para rotar entre los 5 idiomas (EN → KO → JA → ES → PT), o pulsa el botón de idioma en la barra de win rate.", "Tu selección se guarda automáticamente en tu PC para futuras partidas."],
                ["Site: clique no seletor de idioma no canto superior direito (ex.: PT · Português). As prévias, o vídeo, a ajuda e a Análise IA mudam na hora.", "HUD no jogo: clique no logo laranja PACE MAKER no canto superior esquerdo.", "Clique em 'Idioma' no menu para alternar entre os 5 idiomas (EN → KO → JA → ES → PT), ou clique direto no botão de idioma na barra de vitória.", "Sua escolha é salva automaticamente no PC para as próximas partidas."]),
              img="images/help_lang_switch.png"),
        ]},
        {"id": "account", "t": L("Account", "계정", "アカウント", "Cuenta", "Conta"), "items": [
            A("login",
              L("Sign in with Google or Discord", "구글·디스코드로 로그인하기", "Google・Discordでログイン", "Iniciar sesión con Google o Discord", "Entrar com Google ou Discord"),
              L("No separate sign-up or password. The app works without signing in.", "따로 가입하거나 비밀번호를 만들지 않습니다. 앱은 로그인 없이도 씁니다.", "別途の登録やパスワードは不要です。アプリはログインなしでも使えます。", "Sin registro ni contraseña. La app funciona sin iniciar sesión.", "Sem cadastro ou senha. O app funciona sem login."),
              L(["Click [Sign in] at the top right.", "Agree to the required privacy terms.", "Choose Google or Discord. A random activity nickname is set on first sign-in."],
                ["오른쪽 위 [로그인]을 누릅니다.", "개인정보 필수 항목에 동의합니다.", "Google 또는 Discord를 고릅니다. 처음 로그인하면 활동 닉네임이 무작위로 정해집니다."],
                ["右上の［ログイン］を押します。", "必須のプライバシー項目に同意します。", "GoogleかDiscordを選びます。初回ログイン時に活動ニックネームがランダムに決まります。"],
                ["Pulsa [Iniciar sesión] arriba a la derecha.", "Acepta los términos de privacidad obligatorios.", "Elige Google o Discord. En el primer inicio se asigna un apodo aleatorio."],
                ["Clique em [Entrar] no canto superior direito.", "Aceite os termos de privacidade obrigatórios.", "Escolha Google ou Discord. No primeiro login um apelido aleatório é definido."])),
            A("profile",
              L("Change nickname or profile image", "닉네임·프로필 이미지 바꾸기", "ニックネーム・プロフィール画像の変更", "Cambiar apodo o imagen de perfil", "Mudar apelido ou imagem de perfil"),
              L("Both are on My Page.", "둘 다 마이페이지에서 바꿉니다.", "どちらもマイページで変更します。", "Ambos están en Mi página.", "Ambos ficam em Minha página."),
              L(["Click your nickname at the top right to open My Page.", "Nickname: type 2 to 20 characters (or press Random) and click Save.", "Image: click the image on the left and pick a summoner icon or a photo."],
                ["오른쪽 위 닉네임을 눌러 마이페이지를 엽니다.", "닉네임: 2~20자로 입력하고(또는 [랜덤]) [저장]을 누릅니다.", "이미지: 왼쪽 이미지를 눌러 소환사 아이콘이나 앨범 사진을 고릅니다."],
                ["右上のニックネームを押してマイページを開きます。", "ニックネーム：2〜20文字で入力（または［ランダム］）して［保存］を押します。", "画像：左の画像を押してサモナーアイコンか写真を選びます。"],
                ["Pulsa tu apodo arriba a la derecha para abrir Mi página.", "Apodo: escribe de 2 a 20 caracteres (o Aleatorio) y pulsa Guardar.", "Imagen: pulsa la imagen de la izquierda y elige un icono o una foto."],
                ["Clique no seu apelido no canto superior direito para abrir Minha página.", "Apelido: digite de 2 a 20 caracteres (ou Aleatório) e clique em Salvar.", "Imagem: clique na imagem à esquerda e escolha um ícone ou uma foto."])),
            A("delete-account",
              L("Sign out or delete the account", "로그아웃·계정 탈퇴", "ログアウト・アカウント削除", "Cerrar sesión o borrar la cuenta", "Sair ou excluir a conta"),
              L("Deleting removes your account and nickname from our database right away.", "탈퇴하면 저장된 계정 정보와 닉네임이 바로 지워집니다.", "削除すると保存されたアカウント情報とニックネームがすぐ消えます。", "Al borrar, tu cuenta y apodo se eliminan de inmediato.", "Ao excluir, sua conta e apelido são apagados na hora."),
              L(["Open My Page.", "Click [Sign out] to sign out, or [Delete account] and confirm to delete."],
                ["마이페이지를 엽니다.", "[로그아웃]을 누르거나, [계정 탈퇴]를 누르고 확인합니다."],
                ["マイページを開きます。", "［ログアウト］を押すか、［アカウント削除］を押して確認します。"],
                ["Abre Mi página.", "Pulsa [Cerrar sesión] o [Borrar cuenta] y confirma."],
                ["Abra Minha página.", "Clique em [Sair] ou em [Excluir conta] e confirme."])),
        ]},
    ]},
    {"id": "use", "t": L("Using the features", "기능 사용법", "主な機能の使い方", "Uso de las funciones", "Uso dos recursos"), "subs": [
        {"id": "hud", "t": L("In-game HUD", "인게임 HUD", "ゲーム内HUD", "HUD en partida", "HUD na partida"), "items": [
            A("panels",
              L("The four HUD panels", "HUD 패널 4가지", "HUDの4つのパネル", "Los cuatro paneles del HUD", "Os quatro painéis do HUD"),
              L("Each panel floats on its own at the top left, away from LoL's own HUD.", "패널마다 따로 떠 있고, 롤 기본 화면을 피해 왼쪽 위에 모여 있습니다.", "各パネルは個別に表示され、LoL標準UIを避けて左上に並びます。", "Cada panel flota por separado arriba a la izquierda, lejos del HUD del juego.", "Cada painel fica separado no canto superior esquerdo, longe do HUD do jogo."),
              L(["Win chance: my team's win chance, state and game phase.", "Calls: up to 3 things to do now; the top one comes first.", "Briefing: your weakest stat and what is pulling the win rate down.", "Tier check: your value, the average of your tier and role, and your top %."],
                ["승률: 우리 팀 승률·형세·국면.", "오더: 지금 할 일 최대 3개, 위가 먼저입니다.", "브리핑: 가장 약한 지표와 승률을 깎는 요인.", "티어 진단: 내 값, 같은 티어·포지션 평균, 상위 %."],
                ["勝率：味方の勝率・形勢・局面。", "指示：今やること最大3つ、上が優先です。", "ブリーフィング：最も弱い指標と勝率を下げている要因。", "ティア診断：自分の値、同じティア・ロールの平均、上位%。"],
                ["Prob. de victoria: probabilidad de tu equipo, estado y fase.", "Órdenes: hasta 3 acciones; la primera va antes.", "Resumen: tu estadística más débil y lo que baja la victoria.", "Nivel de división: tu valor, la media de tu tier y rol, y tu top %."],
                ["Chance de vitória: do seu time, situação e fase.", "Ordens: até 3 ações agora; a de cima vem primeiro.", "Resumo: sua estatística mais fraca e o que derruba a vitória.", "Nível do elo: seu valor, a média do seu tier e função, e seu top %."]),
              L("Briefing and Tier check are off at first. Turn them on from the logo menu.", "브리핑·티어 진단은 처음엔 꺼져 있습니다. 로고 메뉴에서 켭니다.", "ブリーフィングとティア診断は最初オフです。ロゴメニューでオンにします。", "Resumen y Tier empiezan desactivados; actívalos desde el menú del logo.", "Resumo e Tier começam desligados; ligue no menu do logo."),
              img="images/help_hud_panels.png"),
            A("toggle",
              L("Turn panels on or off and set opacity", "패널 켜고 끄기·투명도", "パネルのオン・オフと透明度", "Activar paneles y opacidad", "Ligar painéis e opacidade"),
              L("Everything is in the menu that opens when you click the logo.", "로고를 누르면 열리는 메뉴에서 모두 바꿉니다.", "ロゴを押すと開くメニューですべて変更します。", "Todo está en el menú que se abre al pulsar el logo.", "Tudo fica no menu que abre ao clicar no logo."),
              L(["Click the PACE MAKER logo.", "Click a panel name to show or hide it (✓ = shown).", "Use − and + next to Opacity. Changes are saved right away."],
                ["PACE MAKER 로고를 누릅니다.", "패널 이름을 눌러 보이기·숨기기를 바꿉니다(✓ = 보임).", "투명도 옆 −, +로 조절합니다. 바꾼 내용은 바로 저장됩니다."],
                ["PACE MAKERロゴを押します。", "パネル名を押して表示・非表示を切り替えます（✓＝表示）。", "透明度の横の−・＋で調整します。変更はすぐ保存されます。"],
                ["Pulsa el logo PACE MAKER.", "Pulsa el nombre de un panel para mostrarlo u ocultarlo (✓ = visible).", "Usa − y + junto a Opacidad. Se guarda al instante."],
                ["Clique no logo PACE MAKER.", "Clique no nome de um painel para mostrar ou ocultar (✓ = visível).", "Use − e + ao lado de Opacidade. Salva na hora."]),
              img="images/help_hud_menu.png"),
            A("move",
              L("Move, lock or reset positions", "위치 옮기기·고정·초기화", "位置の移動・固定・リセット", "Mover, fijar o restablecer posiciones", "Mover, travar ou redefinir posições"),
              L("Drag to move, lock once it is where you want, reset if it gets lost.", "끌어서 옮기고, 자리를 잡으면 고정하고, 잃어버리면 초기화합니다.", "ドラッグで移動、決まったら固定、見失ったらリセットします。", "Arrastra para mover, fija cuando esté bien y restablece si se pierde.", "Arraste para mover, trave quando estiver bom e redefina se sumir."),
              L(["Drag the logo or any panel; its position is saved.", "Click 'Lock position' so it cannot be dragged by accident (click again to unlock).", "Click 'Reset position' to put the logo and every panel back to the default top-left layout."],
                ["로고나 패널을 끌어 옮깁니다. 위치는 저장됩니다.", "'위치 고정'을 누르면 실수로 끌리지 않습니다(다시 누르면 풀림).", "'위치 초기화'를 누르면 로고와 모든 패널이 왼쪽 위 기본 배치로 돌아갑니다."],
                ["ロゴやパネルをドラッグして移動します。位置は保存されます。", "「位置を固定」を押すと誤ってドラッグされません（もう一度押すと解除）。", "「位置をリセット」でロゴと全パネルが左上の初期配置に戻ります。"],
                ["Arrastra el logo o un panel; la posición se guarda.", "Pulsa 'Fijar posición' para no moverlo sin querer (pulsa otra vez para soltar).", "Pulsa 'Restablecer posición' para volver a la disposición inicial arriba a la izquierda."],
                ["Arraste o logo ou um painel; a posição é salva.", "Clique em 'Travar posição' para não mover sem querer (clique de novo para soltar).", "Clique em 'Redefinir posição' para voltar ao layout inicial no canto superior esquerdo."]),
              img="images/help_hud_menu.png"),
            A("keys",
              L("Hotkeys F8 and F9", "단축키 F8·F9", "ショートカット F8・F9", "Atajos F8 y F9", "Atalhos F8 e F9"),
              L("There is no close button, so the HUD cannot be closed by mistake.", "닫기 단추가 없어 실수로 꺼지지 않습니다.", "閉じるボタンがないため、誤って閉じることはありません。", "No hay botón de cerrar, así que no se cierra por error.", "Não há botão de fechar, então ele não fecha sem querer."),
              L(["F8: click-through. Clicks on the HUD go to the game.", "F9: hide or show all panels. The logo stays."],
                ["F8: 클릭 통과. HUD 위를 눌러도 게임에 전달됩니다.", "F9: 패널 전체 숨기기·보이기. 로고는 남습니다."],
                ["F8：クリック透過。HUD上のクリックがゲームに届きます。", "F9：全パネルの非表示・表示。ロゴは残ります。"],
                ["F8: clic a través. Los clics sobre el HUD llegan al juego.", "F9: oculta o muestra todos los paneles. El logo se queda."],
                ["F8: clique através. Cliques no HUD vão para o jogo.", "F9: oculta ou mostra todos os painéis. O logo fica."]),
              img="images/help_hud_menu.png"),
        ]},
        {"id": "read", "t": L("Reading win rate & briefing", "승률·브리핑 읽는 법", "勝率・ブリーフィングの見方", "Leer victoria y resumen", "Ler vitória e resumo"), "items": [
            A("winrate",
              L("How is the win rate calculated?", "승률은 어떻게 계산하나요?", "勝率はどう計算されますか？", "¿Cómo se calcula la victoria?", "Como a vitória é calculada?"),
              L("From the public scoreboard gaps (kills, CS, level, objectives) and the game time.", "공개 점수판의 격차(킬·CS·레벨·오브젝트)와 경기 시간으로 계산합니다.", "公開スコアボードの差（キル・CS・レベル・オブジェクト）と試合時間で計算します。", "Con las diferencias del marcador público (kills, CS, nivel, objetivos) y el tiempo.", "Pelas diferenças do placar público (abates, CS, nível, objetivos) e pelo tempo."),
              L(["When both teams are even, it shows 50%.", "Checked on 124 KR ranked games the model never saw, it picked the winner 71% of the time at 10 min, 79% at 20 min and 87% at 30 min.", "The model was built from ranked Summoner's Rift. In ARAM and other modes, read it as a rough guide."],
                ["두 팀이 비등하면 50%입니다.", "학습에 쓰지 않은 KR 솔로 랭크 124경기로 재 보면 이긴 팀을 맞힌 비율이 10분 71%, 20분 79%, 30분 87%였습니다.", "협곡 솔로 랭크로 만든 모델이라 칼바람·다른 모드에서는 참고용으로 보세요."],
                ["両チームが互角なら50%です。", "学習に使っていないKRランク124試合で測ると、勝者の的中率は10分71%、20分79%、30分87%でした。", "サモナーズリフトのランク戦で作ったモデルのため、ARAMなど他モードでは参考程度に見てください。"],
                ["Si los equipos están igualados, muestra 50%.", "En 124 partidas clasificatorias de KR que el modelo no vio, acertó el ganador el 71% a los 10 min, el 79% a los 20 y el 87% a los 30.", "Está hecho con clasificatorias de la Grieta; en ARAM y otros modos tómalo como guía aproximada."],
                ["Com os times empatados, mostra 50%.", "Em 124 ranqueadas KR que o modelo não viu, acertou o vencedor 71% aos 10 min, 79% aos 20 e 87% aos 30.", "Foi feito com ranqueadas de Summoner's Rift; em ARAM e outros modos use como guia aproximado."]),
              img="images/feat_winrate.png"),
            A("calls",
              L("Which call comes first?", "오더는 어떤 순서로 나오나요?", "指示はどの順で出ますか？", "¿Qué orden tienen las órdenes?", "Qual a ordem das ordens?"),
              L("Urgent fights first, then objectives, then recall and counter items.", "급한 교전이 먼저, 다음이 오브젝트, 그다음이 귀환·카운터 아이템입니다.", "急ぎの戦闘が先、次にオブジェクト、その後リコール・カウンターアイテムです。", "Primero las peleas urgentes, luego objetivos, luego volver y objetos de respuesta.", "Primeiro lutas urgentes, depois objetivos, depois voltar e itens de resposta."),
              L(["Numbers advantage, smite advantage or danger come first.", "Dragon, Baron and Voidgrubs calls appear only while the objective is up.", "When a defensive call is shown, attack calls are removed so they never conflict.", "Recall is suggested at 1,300 gold or more (ARAM: reset after a fight at 2,600), not while you are dead."],
                ["수적 우위·열세, 강타 우위·위험이 먼저입니다.", "드래곤·바론·공허 유충 콜은 그 오브젝트가 살아 있을 때만 나옵니다.", "수비 콜이 있으면 공격 콜은 빼서 서로 부딪치지 않게 합니다.", "귀환은 1,300골드 이상일 때 권합니다(칼바람은 2,600골드에서 교전 뒤 정비). 죽어 있을 때는 내지 않습니다."],
                ["人数有利・不利、スマイト有利・危険が先です。", "ドラゴン・バロン・ヴォイドグラブの指示は、そのオブジェクトがいる間だけ出ます。", "守備の指示が出ている時は攻撃の指示を消して矛盾させません。", "リコールは1,300ゴールド以上で勧めます（ARAMは2,600で戦闘後に整備）。デス中は出しません。"],
                ["Primero ventaja o desventaja numérica y de castigo.", "Las órdenes de dragón, Barón y larvas solo salen si el objetivo está vivo.", "Si hay una orden defensiva, se quitan las de ataque para no contradecirse.", "Volver se sugiere con 1.300 de oro o más (ARAM: reajustar tras pelea con 2.600), no estando muerto."],
                ["Primeiro vantagem ou desvantagem numérica e de golpear.", "Ordens de dragão, Barão e larvas só aparecem com o objetivo vivo.", "Se houver ordem defensiva, as de ataque saem para não conflitar.", "Voltar é sugerido com 1.300 de ouro ou mais (ARAM: reajustar após luta com 2.600), não quando morto."]),
              img="images/feat_calls.png"),
            A("tier",
              L("Reading the tier check", "티어 진단 읽기", "ティア診断の見方", "Leer el diagnóstico de tier", "Ler o diagnóstico de tier"),
              L("Example: 'CS/min 7.6 · avg 7.9 (top 63%)'. Lower top % is better.", "예: '분당 CS 7.6 · 평균 7.9 (상위 63%)'. 상위 %가 작을수록 좋습니다.", "例：「分間CS 7.6・平均 7.9（上位63%）」。上位%が小さいほど良いです。", "Ejemplo: 'CS/min 7.6 · media 7.9 (top 63%)'. Menor top % es mejor.", "Exemplo: 'CS/min 7,6 · média 7,9 (top 63%)'. Top % menor é melhor."),
              L(["The average comes from real KR games of your tier, your role and the nearest minute (10, 15, 20 or 25).", "It appears from 8 minutes on, on Summoner's Rift only.", "Top % uses a normal-distribution approximation."],
                ["평균은 같은 티어·포지션·가까운 시점(10·15·20·25분)의 KR 실제 경기에서 나옵니다.", "8분부터, 소환사의 협곡에서만 나옵니다.", "상위 %는 정규분포 근사입니다."],
                ["平均は同じティア・ロール・近い時点（10・15・20・25分）のKR実戦から出しています。", "8分以降、サモナーズリフトでのみ表示されます。", "上位%は正規分布による近似です。"],
                ["La media sale de partidas reales de KR de tu tier, tu rol y el minuto más cercano (10, 15, 20 o 25).", "Aparece desde el minuto 8 y solo en la Grieta.", "El top % es una aproximación normal."],
                ["A média vem de partidas reais KR do seu tier, sua função e do minuto mais próximo (10, 15, 20 ou 25).", "Aparece a partir dos 8 minutos e só em Summoner's Rift.", "O top % é uma aproximação normal."]),
              img="images/feat_briefing.png"),
        ]},
        {"id": "review", "t": L("Match search & AI analysis", "전적 검색·AI 분석", "戦績検索・AI分析", "Historial y análisis IA", "Histórico e análise IA"), "items": [
            A("search",
              L("Search a match history", "전적 검색하기", "戦績を検索", "Buscar un historial", "Buscar um histórico"),
              L("Anyone can search by Riot ID.", "Riot ID로 누구나 검색할 수 있습니다.", "Riot IDで誰でも検索できます。", "Cualquiera puede buscar por Riot ID.", "Qualquer um pode buscar pelo Riot ID."),
              L(["Pick the server (for example KR) in the search bar at the top.", "Type GameName#KR1 and click [Search].", "Recent matches are listed; click [AI Analysis] on a match to analyze it."],
                ["위 검색 칸에서 서버(예: KR)를 고릅니다.", "GameName#KR1 형식으로 입력하고 [검색]을 누릅니다.", "최근 경기가 나오면 경기의 [AI 분석]을 눌러 확인합니다."],
                ["上の検索欄でサーバー（例：KR）を選びます。", "GameName#KR1形式で入力して［検索］を押します。", "最近の試合が出たら、試合の［AI分析］で確認します。"],
                ["Elige el servidor (por ejemplo KR) en la barra superior.", "Escribe GameName#KR1 y pulsa [Buscar].", "Aparecen las partidas recientes; pulsa [Análisis IA] para analizar una."],
                ["Escolha o servidor (por exemplo KR) na barra superior.", "Digite GameName#KR1 e clique em [Buscar].", "As partidas recentes aparecem; clique em [Análise IA] para analisar uma."]),
              img="images/help_review.png"),
            A("review-read",
              L("Reading the AI analysis report", "AI 분석 리포트 읽기", "AI分析レポートの見方", "Leer el informe de análisis IA", "Ler o relatório de análise IA"),
              L("Your numbers are compared with real ranked games of the same role and game length.", "내 수치를 같은 포지션·비슷한 경기 길이의 실제 랭크 경기 평균과 쉬운 말로 비교합니다.", "自分の数値を同じロール・近い試合時間の実際のランク戦平均とわかりやすく比べます。", "Tus números se comparan con el promedio de partidas clasificatorias reales del mismo rol y duración.", "Seus números são comparados com a média de ranqueadas reais da mesma função e duração."),
              L(["01: Overall match position and typical averages for your role (CS/min, gold diff, vision, deaths, damage).", "02: Six-axis chart (dashed ring = average) with your actual numbers and rank.", "Strengths and fixes highlight only the areas clearly above or below the role average.", "Key swing: the 3-minute window where the team gold gap changed most, with your deaths and lost objectives.", "03: The costliest deaths, with objectives lost in the next 60 seconds and your wards in the 3 minutes before."],
                ["01: 이번 판 종합 위치와 같은 포지션 평균(분당 CS·골드 차이·시야·데스·딜 비중)을 쉬운 문장으로 보여 줍니다.", "02: 6각형 그래프(점선 = 평균)와 6개 영역의 내 실제 기록·순위를 보여 줍니다.", "잘한 점과 개선점은 같은 포지션 평균보다 뚜렷하게 앞섰거나 아쉬웠던 항목만 골라 알려 줍니다.", "승기가 기울었던 3분 구간과 그 안의 내 데스·잃은 오브젝트를 함께 정리합니다.", "03: 손해가 컸던 데스. 사망 뒤 60초 안에 잃은 오브젝트, 직전 3분 내 와드 수를 함께 씁니다."],
                ["01：試合全体の順位と、同じロールの平均（分あたりCS・ゴールド差・視界・デス・ダメージ割合）。", "02：6角形チャート（点線＝平均）と6領域の実測値・順位。", "良かった点と改善点は平均より明確に高い・低い項目だけを示します。", "勝敗が傾いた3分間と、その間の自分のデス・失ったオブジェクトをまとめます。", "03：損失が大きかったデス。デス後60秒以内に失ったオブジェクトと直前3分のワード数を示します。"],
                ["01: Posición general y promedios típicos de tu rol (CS/min, diferencia de oro, visión, muertes, daño).", "02: Gráfico hexagonal (línea discontinua = promedio) con tus números reales y posición.", "Puntos fuertes y mejoras muestran solo las áreas claramente por encima o por debajo del promedio.", "El tramo clave de 3 minutos donde más cambió el oro del equipo, con tus muertes y objetivos perdidos.", "03: Las muertes más caras, con objetivos perdidos en 60 s y tus guardianes en los 3 min previos."],
                ["01: Posição geral e médias típicas da sua função (CS/min, diferença de ouro, visão, mortes, dano).", "02: Gráfico hexagonal (linha tracejada = média) com seus números reais e posição.", "Pontos fortes e correções mostram apenas áreas claramente acima ou abaixo da média.", "O trecho decisivo de 3 minutos em que o ouro do time mais mudou, com suas mortes e objetivos perdidos.", "03: As mortes mais caras, com objetivos perdidos em 60 s e suas sentinelas nos 3 min anteriores."]),
              L("Positions come from 1-minute snapshots, and skill shots or enemy wards are not in the data, so the review does not say who was at fault.", "위치는 1분 간격 기록이고 스킬 적중·상대 와드는 데이터에 없어, 누구 잘못인지는 단정하지 않습니다.", "位置は1分間隔の記録で、スキル命中や相手のワードはデータにないため、誰の責任かは断定しません。", "Las posiciones son instantáneas de 1 minuto y no hay datos de habilidades ni guardianes enemigos, así que no se culpa a nadie.", "As posições são registros de 1 minuto e não há dados de habilidades nem sentinelas inimigas, então não se culpa ninguém."),
              img="images/help_review.png"),
            A("excluded",
              L("Games the review skips or treats differently", "분석에서 빠지거나 다르게 보는 경기", "分析対象外・扱いが異なる試合", "Partidas que el repaso omite o trata distinto", "Partidas que a revisão ignora ou trata diferente"),
              L("Remakes are skipped; ARAM is compared with ARAM.", "다시하기 경기는 빼고, 칼바람은 칼바람끼리 비교합니다.", "リメイクは除外し、ARAMはARAM同士で比べます。", "Las repeticiones se omiten; ARAM se compara con ARAM.", "Remakes são ignorados; ARAM é comparado com ARAM."),
              L(["Games shorter than 5 minutes or ended by early surrender are not analyzed.", "ARAM uses economy and frontline instead of laning and vision.", "Modes without the usual 5v5 teams (for example Arena) are not supported."],
                ["5분이 안 된 경기와 조기 항복 경기는 분석하지 않습니다.", "칼바람은 라인전 대신 경제, 시야 대신 탱킹을 봅니다.", "아레나처럼 5대5 팀 구성이 아닌 모드는 지원하지 않습니다."],
                ["5分未満の試合と早期降伏の試合は分析しません。", "ARAMはレーン戦の代わりに経済、視界の代わりにタンキングを見ます。", "アリーナなど5対5でないモードは対象外です。"],
                ["No se analizan partidas de menos de 5 minutos ni rendiciones tempranas.", "En ARAM se mira economía y aguante en vez de línea y visión.", "Los modos sin equipos 5v5 (por ejemplo Arena) no se admiten."],
                ["Partidas com menos de 5 minutos ou rendição antecipada não são analisadas.", "No ARAM se usa economia e linha de frente em vez de rota e visão.", "Modos sem times 5x5 (por exemplo Arena) não são suportados."])),
        ]},
    ]},
    {"id": "trouble", "t": L("Troubleshooting", "문제 해결", "トラブル解決", "Solución de problemas", "Solução de problemas"), "subs": [
        {"id": "nohud", "t": L("The HUD does not show", "HUD가 안 보여요", "HUDが見えない", "No se ve el HUD", "O HUD não aparece"), "items": [
            A("borderless",
              L("The HUD does not appear on top of the game", "게임 위에 HUD가 안 떠요", "ゲームの上にHUDが出ない", "El HUD no aparece sobre el juego", "O HUD não aparece sobre o jogo"),
              L("Exclusive fullscreen hides every overlay. Switch LoL to Borderless.", "롤 화면이 '전체 화면'이면 가려집니다. '테두리 없음'으로 바꿉니다.", "LoLが「フルスクリーン」だと隠れます。「ボーダーレス」に変えます。", "La pantalla completa exclusiva oculta el HUD. Cambia LoL a Sin bordes.", "A tela cheia exclusiva esconde o HUD. Mude o LoL para Sem bordas."),
              L(["In LoL open [Settings → Video → Window Mode].", "Choose Borderless and apply.", "The HUD shows from the next poll (about 2 seconds)."],
                ["롤에서 [설정 → 화면 → 창 모드]를 엽니다.", "'테두리 없음'을 고르고 적용합니다.", "약 2초 뒤 다음 갱신부터 HUD가 보입니다."],
                ["LoLで［設定 → ビデオ → ウィンドウモード］を開きます。", "「ボーダーレス」を選んで適用します。", "約2秒後の次の更新からHUDが表示されます。"],
                ["En LoL abre [Ajustes → Vídeo → Modo de ventana].", "Elige Sin bordes y aplica.", "El HUD aparece en la siguiente lectura (unos 2 segundos)."],
                ["No LoL abra [Configurações → Vídeo → Modo de janela].", "Escolha Sem bordas e aplique.", "O HUD aparece na próxima leitura (cerca de 2 segundos)."])),
            A("waiting",
              L("A match is running but the HUD stays hidden", "경기 중인데 HUD가 안 나와요", "試合中なのにHUDが出ない", "Hay partida pero el HUD no sale", "A partida começou mas o HUD não aparece"),
              L("The game's local API opens only after the loading screen.", "게임의 로컬 API는 로딩 화면이 끝난 뒤에 열립니다.", "ゲームのローカルAPIはロード画面の後に開きます。", "La API local del juego se abre tras la pantalla de carga.", "A API local do jogo só abre depois da tela de carregamento."),
              L(["Wait until the loading screen ends and the match starts.", "Check that the app is running (auto-start on, or start it once yourself).", "Check that no firewall blocks 127.0.0.1 port 2999."],
                ["로딩 화면이 끝나고 경기가 시작될 때까지 기다립니다.", "앱이 실행 중인지 확인합니다(자동 실행을 켜 두거나 한 번 직접 실행).", "방화벽이 127.0.0.1의 2999 포트를 막지 않는지 확인합니다."],
                ["ロード画面が終わり試合が始まるまで待ちます。", "アプリが起動しているか確認します（自動起動をオン、または一度手動で起動）。", "ファイアウォールが127.0.0.1のポート2999を塞いでいないか確認します。"],
                ["Espera a que termine la carga y empiece la partida.", "Comprueba que la app está abierta (inicio automático o ábrela una vez).", "Comprueba que ningún cortafuegos bloquea 127.0.0.1 puerto 2999."],
                ["Espere a tela de carregamento terminar e a partida começar.", "Confira se o app está aberto (início automático ou abra uma vez).", "Confira se nenhum firewall bloqueia 127.0.0.1 porta 2999."]),
              L("After a match ends the HUD hides about 6 seconds later. That is normal.", "경기가 끝나면 약 6초 뒤 HUD가 숨습니다. 정상입니다.", "試合終了の約6秒後にHUDは隠れます。正常です。", "Al acabar la partida el HUD se oculta a los 6 segundos aprox. Es normal.", "Quando a partida acaba o HUD some em cerca de 6 segundos. É normal."),
              img="images/app_hud.png"),
            A("lost",
              L("The HUD moved off screen", "HUD가 화면 밖으로 사라졌어요", "HUDが画面外に消えた", "El HUD se salió de la pantalla", "O HUD saiu da tela"),
              L("Reset the layout from the logo menu.", "로고 메뉴에서 위치를 초기화합니다.", "ロゴメニューで位置をリセットします。", "Restablece la disposición desde el menú del logo.", "Redefina o layout pelo menu do logo."),
              L(["Press F9 to make sure the panels are not just hidden.", "Click the PACE MAKER logo and choose 'Reset position'.", "If even the logo cannot be found, send us a message below."],
                ["먼저 F9를 눌러 패널이 숨김 상태인지 확인합니다.", "PACE MAKER 로고를 누르고 '위치 초기화'를 고릅니다.", "로고까지 안 보이면 아래 문의로 알려 주세요."],
                ["まずF9でパネルが非表示になっていないか確認します。", "PACE MAKERロゴを押し「位置をリセット」を選びます。", "ロゴも見えない場合は下の問い合わせからお知らせください。"],
                ["Pulsa F9 para comprobar que los paneles no están solo ocultos.", "Pulsa el logo PACE MAKER y elige 'Restablecer posición'.", "Si no encuentras ni el logo, escríbenos abajo."],
                ["Aperte F9 para ver se os painéis não estão só ocultos.", "Clique no logo PACE MAKER e escolha 'Redefinir posição'.", "Se nem o logo aparecer, fale conosco abaixo."]),
              img="images/help_hud_menu.png"),
        ]},
        {"id": "errors", "t": L("Install & run errors", "설치·실행 오류", "インストール・起動のエラー", "Errores de instalación", "Erros de instalação"), "items": [
            A("security",
              L("A security warning appears when installing", "설치할 때 보안 경고가 떠요", "インストール時にセキュリティ警告が出る", "Aparece un aviso de seguridad al instalar", "Aparece um aviso de segurança ao instalar"),
              L("The app has no paid code-signing certificate, so Windows and macOS warn once.", "유료 코드 서명 인증서가 없어 Windows·macOS가 한 번 경고합니다.", "有料のコード署名証明書がないため、WindowsとmacOSが一度警告します。", "La app no tiene certificado de firma de pago, así que el sistema avisa una vez.", "O app não tem certificado de assinatura pago, então o sistema avisa uma vez."),
              L(["Windows: [More info → Run anyway].", "macOS: [System Settings → Privacy & Security] → [Open Anyway].", "To be sure the file is the original, compare its SHA-256 (see 'Check that the download is the original')."],
                ["Windows: [추가 정보 → 실행].", "macOS: [시스템 설정 → 개인정보 보호 및 보안] → [그래도 열기].", "원본인지 확인하려면 SHA-256을 비교하세요('받은 파일이 원본인지 확인하기' 참고)."],
                ["Windows：［詳細情報 → 実行］。", "macOS：［システム設定 → プライバシーとセキュリティ］→［このまま開く］。", "本物か確かめるにはSHA-256を比べてください（「ダウンロードしたファイルが本物か確認」参照）。"],
                ["Windows: [Más información → Ejecutar de todas formas].", "macOS: [Ajustes del Sistema → Privacidad y seguridad] → [Abrir igualmente].", "Para confirmar que es el original, compara su SHA-256 (ver 'Comprobar que la descarga es la original')."],
                ["Windows: [Mais informações → Executar assim mesmo].", "macOS: [Ajustes do Sistema → Privacidade e Segurança] → [Abrir Mesmo Assim].", "Para confirmar que é o original, compare o SHA-256 (veja 'Conferir se o download é o original')."])),
            A("autostart-fail",
              L("It does not start when I turn on the PC", "컴퓨터를 켜도 자동으로 안 켜져요", "PCを起動しても自動で起動しない", "No se inicia al encender el PC", "Não inicia ao ligar o PC"),
              L("Check the auto-start switch in the app.", "앱의 자동 실행 설정을 확인합니다.", "アプリの自動起動設定を確認します。", "Revisa el interruptor de inicio automático en la app.", "Confira a opção de início automático no app."),
              L(["Start the app once yourself and join a match.", "In the logo menu, make sure 'Start with computer' has a ✓.", "Windows: also check that LoLPacemaker is enabled in [Task Manager → Startup apps]."],
                ["앱을 한 번 직접 실행하고 경기에 들어갑니다.", "로고 메뉴에서 '컴퓨터 켤 때 자동 실행'에 ✓가 있는지 확인합니다.", "Windows는 [작업 관리자 → 시작 앱]에서 LoLPacemaker가 사용으로 되어 있는지도 확인합니다."],
                ["アプリを一度手動で起動して試合に入ります。", "ロゴメニューの「PC起動時に自動実行」に✓があるか確認します。", "Windowsでは［タスクマネージャー → スタートアップアプリ］でLoLPacemakerが有効かも確認します。"],
                ["Abre la app una vez y entra en una partida.", "En el menú del logo, comprueba que 'Iniciar con el ordenador' tiene ✓.", "En Windows, comprueba también que LoLPacemaker está habilitado en [Administrador de tareas → Aplicaciones de arranque]."],
                ["Abra o app uma vez e entre numa partida.", "No menu do logo, confira se 'Iniciar com o computador' tem ✓.", "No Windows, confira também se LoLPacemaker está habilitado em [Gerenciador de Tarefas → Aplicativos de inicialização]."]),
              img="images/help_hud_menu.png"),
            A("update",
              L("How do updates work?", "업데이트는 어떻게 하나요?", "アップデートはどうなりますか？", "¿Cómo se actualiza?", "Como funcionam as atualizações?"),
              L("The win-rate model updates by itself; installing a newer app keeps your saved settings.", "승률 모델은 저절로 바뀌고, 앱을 새로 설치해도 기존 설정과 위치는 유지됩니다.", "勝率モデルは自動で更新され、アプリを上書きしても設定と位置は残ります。", "El modelo se actualiza solo; al reinstalar la app se conservan tus ajustes.", "O modelo se atualiza sozinho; ao reinstalar o app suas configurações são mantidas."),
              L(["At start the app checks for a newer verified model in the background; it is used from the next launch.", "For a new app version, download it again from [Download app] and install over the old one.", "Your saved panel layout, language, and opacity stay intact across updates."],
                ["앱이 시작할 때 검증된 새 모델이 있는지 뒤에서 확인하고, 다음 실행부터 씁니다.", "앱 새 버전은 [앱 다운로드]에서 다시 받아 기존 앱 위에 설치합니다.", "기존에 저장해 둔 패널 위치·언어·투명도 설정은 업데이트 후에도 그대로 유지됩니다."],
                ["起動時に検証済みの新しいモデルがあるか裏で確認し、次回起動から使います。", "アプリの新バージョンは［アプリをダウンロード］から入手し、上書きインストールします。", "保存したパネル配置・言語・透明度の設定はアップデート後も維持されます。"],
                ["Al iniciar, la app busca en segundo plano un modelo verificado nuevo; se usa desde el siguiente inicio.", "Para una versión nueva, descárgala en [Descargar app] e instálala encima.", "Tu diseño de paneles, idioma y opacidad guardados se mantienen tras actualizar."],
                ["Ao iniciar, o app procura em segundo plano um modelo verificado novo; ele é usado a partir da próxima execução.", "Para uma versão nova, baixe em [Baixar app] e instale por cima.", "Seu layout de painéis, idioma e opacidade salvos são mantidos após atualizar."])),
            A("click-through",
              L("My mouse clicks hit the HUD during a fight", "교전 중 클릭이 HUD에 걸려요 (F8 클릭 투과)", "戦闘中にクリックがHUDに当たる（F8クリック透過）", "Mis clics tocan el HUD en pelea (F8 clic pasante)", "Meus cliques acertam o HUD na luta (F8 clique atravessável)"),
              L("Press F8 to let mouse clicks pass straight through the HUD to the game.", "F8을 누르면 마우스 클릭이 HUD를 통과해 게임으로 바로 전달됩니다.", "F8を押すとマウスクリックがHUDを通り抜けてゲームに届きます。", "Pulsa F8 para que los clics atraviesen el HUD hacia el juego.", "Pressione F8 para que os cliques atravessem o HUD direto para o jogo."),
              L(["Press F8 once during a match to enable click-through.", "Press F8 again when you want to drag panels or open the logo menu.", "You can also turn on 'Lock position' in the logo menu so panels never move by accident."],
                ["경기 중 F8을 한 번 누르면 클릭 투과가 켜져 HUD 위를 눌러도 게임만 클릭됩니다.", "패널을 옮기거나 로고 메뉴를 열 때는 F8을 한 번 더 눌러 해제합니다.", "평소에 로고 메뉴에서 '위치 고정'을 켜 두면 실수로 패널이 밀리지 않습니다."],
                ["試合中にF8を1回押すとクリック透過がオンになり、HUDの上からでもゲームを操作できます。", "パネルを動かすときやメニューを開くときはF8をもう一度押して解除します。", "普段からロゴメニューで「位置を固定」をオンにすると誤移動を防げます。"],
                ["Pulsa F8 una vez en partida para activar el modo clic pasante.", "Pulsa F8 de nuevo cuando quieras mover paneles o abrir el menú del logo.", "También puedes activar 'Bloquear posición' en el menú del logo."],
                ["Pressione F8 uma vez na partida para ativar o clique atravessável.", "Pressione F8 novamente quando quiser mover painéis ou abrir o menu.", "Você também pode ativar 'Travar posição' no menu do logo."]),
              img="images/help_hud_menu.png"),
            A("api-temp",
              L("Match history or AI analysis temporarily fails", "전적 검색·AI 분석이 갑자기 안 될 때", "戦績検索やAI分析が一時的に動かない", "El historial o análisis IA falla temporalmente", "O histórico ou análise IA falha temporariamente"),
              L("Web lookup depends on Riot's API key and rate limits; the in-game HUD still works offline.", "웹 전적·복기는 Riot API 키 갱신이나 호출 제한의 영향을 받을 수 있으며, 인게임 HUD는 영향 없이 동작합니다.", "ウェブの戦績・復習はRiot APIキー更新や制限の影響を受けることがありますが、試合中HUDは動作します。", "La búsqueda web depende de la API de Riot; el HUD en partida sigue funcionando sin conexión.", "A busca web depende da API da Riot; o HUD em partida continua funcionando offline."),
              L(["Wait 1–2 minutes and retry if Riot's rate limit was reached.", "Double-check the exact GameName#Tag format and region.", "If the error persists, send a short report below so the Riot API key can be refreshed."],
                ["잠시 호출이 몰린 경우 1~2분 뒤 다시 검색해 주세요.", "닉네임#태그 형식과 서버(KR 등)가 맞는지 확인합니다.", "계속 오류가 나면 아래 문의 칸으로 알려주시면 API 키 상태를 바로 점검합니다."],
                ["アクセスが集中している場合は1〜2分後に再試行してください。", "GameName#Tag形式とサーバー（JP/KRなど）が合っているか確認します。", "エラーが続く場合は下のお問い合わせからご連絡ください。"],
                ["Espera 1–2 minutos y vuelve a intentarlo si se alcanzó el límite de Riot.", "Comprueba el formato Nombre#Tag y la región.", "Si continúa el error, avísanos abajo para revisar la clave de API."],
                ["Aguarde 1–2 minutos e tente novamente se o limite da Riot foi atingido.", "Confira o formato Nome#Tag e a região.", "Se o erro continuar, envie um aviso abaixo para verificarmos a chave de API."])),
        ]},
        {"id": "policy", "t": L("Policy & privacy", "정책·개인정보", "ポリシー・個人情報", "Política y privacidad", "Política e privacidade"), "items": [
            A("ban",
              L("Can I get banned for using it?", "정지 위험은 없나요?", "使うとBANされますか？", "¿Me pueden suspender por usarla?", "Posso ser banido por usar?"),
              L("It only reads Riot's official local API for the public scoreboard and events.", "Riot 공식 로컬 API의 공개 점수판·이벤트만 읽습니다.", "Riot公式ローカルAPIの公開スコアボードとイベントだけを読みます。", "Solo lee la API local oficial de Riot: marcador público y eventos.", "Só lê a API local oficial da Riot: placar público e eventos."),
              L(["The only source is 127.0.0.1:2999 on your own PC, read-only.", "No memory reading, no screen reading (OCR), no hidden information such as enemy wards or cooldowns."],
                ["읽는 곳은 내 PC의 127.0.0.1:2999 하나이고 읽기만 합니다.", "메모리 읽기, 화면 글자 인식(OCR), 상대 와드·쿨타임 같은 숨은 정보는 쓰지 않습니다."],
                ["読むのは自分のPCの127.0.0.1:2999だけで、読み取り専用です。", "メモリ読み取り、画面認識（OCR）、相手のワードやクールダウンなどの隠れた情報は使いません。"],
                ["La única fuente es 127.0.0.1:2999 en tu PC, solo lectura.", "Sin leer memoria, sin reconocimiento de pantalla (OCR), sin información oculta como guardianes o enfriamientos enemigos."],
                ["A única fonte é 127.0.0.1:2999 no seu PC, só leitura.", "Sem ler memória, sem leitura de tela (OCR), sem informação oculta como sentinelas ou recargas inimigas."])),
            A("data",
              L("Stop sharing anonymous numbers and delete them", "익명 수치 제공 끄기·삭제", "匿名数値の提供停止と削除", "Dejar de compartir números anónimos y borrarlos", "Parar de compartilhar números anônimos e apagar"),
              L("Turning it off also asks the server to delete what was sent from this install.", "끄면 이 설치에서 보낸 기록의 삭제도 함께 요청합니다.", "オフにすると、このインストールから送った記録の削除も依頼します。", "Al desactivarlo también se pide borrar lo enviado desde esta instalación.", "Ao desligar também é pedido apagar o que foi enviado desta instalação."),
              L(["Open the logo menu and click 'Share anonymous stats' so the ✓ disappears.", "What was sent: a random install ID, app version, language, tier range, match numbers and the result. Never your name, Riot ID or chat."],
                ["로고 메뉴에서 '익명 수치 제공'을 눌러 ✓를 없앱니다.", "보낸 것: 무작위 설치 ID, 앱 버전, 언어, 티어 구간, 경기 수치와 결과. 이름·Riot ID·채팅은 보내지 않습니다."],
                ["ロゴメニューで「匿名数値の提供」を押して✓を外します。", "送ったもの：ランダムなインストールID、アプリのバージョン、言語、ティア帯、試合の数値と結果。名前・Riot ID・チャットは送りません。"],
                ["Abre el menú del logo y pulsa 'Compartir datos anónimos' para quitar el ✓.", "Lo enviado: un ID de instalación aleatorio, versión, idioma, rango de tier, números de la partida y el resultado. Nunca tu nombre, Riot ID ni chat."],
                ["Abra o menu do logo e clique em 'Compartilhar dados anônimos' para tirar o ✓.", "O que foi enviado: um ID de instalação aleatório, versão, idioma, faixa de tier, números da partida e o resultado. Nunca seu nome, Riot ID ou chat."]),
              img="images/help_hud_menu.png"),
            A("privacy",
              L("Privacy policy", "개인정보 처리방침", "個人情報の取り扱い", "Política de privacidad", "Política de privacidade"),
              L("The full policy is on a separate page.", "전문은 따로 된 페이지에 있습니다.", "全文は別ページにあります。", "La política completa está en otra página.", "A política completa está em outra página."),
              L(["Open the policy page with the link below."],
                ["아래 링크로 처리방침 페이지를 엽니다."],
                ["下のリンクからページを開きます。"],
                ["Abre la página con el enlace de abajo."],
                ["Abra a página pelo link abaixo."]),
              link="privacy.html"),
        ]},
    ]},
]


def check() -> list[str]:
    """Every localized field has the 5 languages; steps have the same count in every language; images exist."""
    bad = []
    for c in HELP:
        for s in c["subs"]:
            for a in s["items"]:
                for f in ("t", "s", "st", "n"):
                    if f in a and set(a[f]) != set(LANGS):
                        bad.append(f"{a['id']}.{f} languages")
                if len({len(a["st"][lang]) for lang in LANGS}) != 1:
                    bad.append(f"{a['id']}.st step counts differ")
                if "img" in a and not (ROOT / "docs" / a["img"]).exists():
                    bad.append(f"{a['id']} missing image {a['img']}")
                if "link" in a and not (ROOT / "docs" / a["link"]).exists():
                    bad.append(f"{a['id']} missing link {a['link']}")
    return bad


def write() -> None:
    block = "const HELP = " + json.dumps(HELP, ensure_ascii=False, separators=(",", ":")) + ";"
    for name in ("docs/index.html", "web/index.html"):
        p = ROOT / name
        s = p.read_text(encoding="utf-8")
        new, n = re.subn(r"const HELP = .*?;\n", lambda _: block + "\n", s, count=1, flags=re.S)
        if n != 1:
            raise SystemExit(f"{name}: no `const HELP = …;` line to replace")
        p.write_text(new, encoding="utf-8")


if __name__ == "__main__":
    problems = check()
    if problems:
        raise SystemExit("\n".join(problems))
    write()
    n = sum(len(s["items"]) for c in HELP for s in c["subs"])
    print(f"categories {len(HELP)} · subcategories {sum(len(c['subs']) for c in HELP)} · articles {n}")
