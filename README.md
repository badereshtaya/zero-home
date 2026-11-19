# زيرو للبيت — Zero Home

<div dir="rtl">

## بالعربي

**زيرو للبيت** بيعطي Home Assistant اللي بالبيت عنوان عام على نطاقنا
(`https://<اسمك>.home.eshtaya.ps`) وبيخلي زيرو يتحكم بالبيت من أي مكان —
بدون فتح منافذ بالراوتر، بدون إعدادات، وبدون دومين خاص فيك. الإضافة بتتصل
**من البيت لبرا** بخادم الربط تبع زيرو، فما في إشي بيدخل على شبكتك من برا.

### المتطلبات

- Home Assistant إصدار 2024.8 أو أحدث.
- اشتراك بزيرو بيشمل «زيرو للبيت».

### التركيب

**عن طريق HACS (مستودع مخصص):**

1. HACS ← القائمة (⋮) ← Custom repositories.
2. حط عنوان المستودع واختار النوع **Integration**.
3. دوّر على «Zero Home» واضغط Download، وبعدها أعد تشغيل Home Assistant.

**يدوي:**

1. انسخ المجلد `custom_components/zero_home` لمجلد `config/custom_components/` عندك.
2. أعد تشغيل Home Assistant.

### تسجيل الدخول (نسخة 1.3.0)

الإعدادات ← الأجهزة والخدمات ← إضافة تكامل ← **Zero Home**، واختار:

- **باسم المستخدم وكلمة السر** تبعك بزيرو، أو
- **بكود QR:** بيطلع كود على الشاشة — امسحه بكاميرا جوالك أو افتح `zero.eshtaya.ps/pair` واكتب الكود، ووافق من حسابك. الكود بيتجدد لحاله والشاشة بتكمّل لحالها.

لازم باقتك تشمل البيت الذكي. عنوان بيتك: `https://<اسم المستخدم>.home.eshtaya.ps`.

### بدون إعدادات جوّا Home Assistant

- الإضافة **بتعرض بس**: الاتصال، عنوان البيت، وحساب زيرو (الاسم، الاشتراك، الأيام الباقية)، وزر بيفتح حسابك.
- **كل الإعدادات من حسابك على موقع زيرو** (حسابي ← البيت الذكي): شو بيشوف زيرو (كل إشي / غرف / أنواع / أجهزة)، وفتح واجهة Home Assistant من برا (مطفي افتراضياً). الإضافة بتقرا التغييرات خلال دقيقة.
- **ما في زر خروج.** بتضل داخلة لحد ما تطلّعها من حسابك ← الأجهزة. وقتها الربط بيوقف وHome Assistant بيطلب «ادخل من جديد» (بنفس الحساب).
- من نسخة قديمة: بعد التحديث بتطلب تسجيل دخول مرة وحدة، والعنوان بيضل نفسه.
- **ما في داعي لأي تعديل على configuration.yaml.** للأمان: إذا في مستخدم «محلي بس» أو «trusted networks»، الواجهة من برا بتضل مسكّرة وبيطلعلك تنبيه بيشرح؛ الـ webhooks ما بتنفتح من برا؛ ومحاولات الدخول الكتير بتنوقف مؤقتاً.

### الخصوصية — شو بيمر عن طريق الربط

- **تحكم زيرو:** طلبات محددة (قراءة حالة، تشغيل خدمة) على الأجهزة المسموحة **بس**. ما بنستعمل ولا بنعمل أي token لـ Home Assistant؛ أي جهاز مش مسموح بيترفض.
- **الواجهة من برا** (إذا مفعّلة): طلباتك أنت من المتصفح أو التطبيق بتمر مشفّرة لحد خادمنا وبعدها للبيت. تسجيل الدخول هو تسجيل دخول Home Assistant نفسه.
- خادم الربط **ما بيخزّن ولا بيسجّل** محتوى الطلبات أو الـ headers؛ بيسجّل بس أوقات الاتصال وعدد البايتات (للحد الشهري).
- الإضافة بتبعت لخادمنا: إصدار Home Assistant، معرّف النسخة، اسم البيت، وعدد الأجهزة المسموحة.

### حل المشاكل

| المشكلة | الحل |
|---|---|
| «ما قدرنا نوصل لزيرو» وقت الدخول | تأكد إنه Home Assistant متصل بالإنترنت وإنه الوقت مضبوط. |
| الحساس «Zero Home» مطفي | شوف سمة `last_error` فيه. الإضافة بتحاول لحالها (1، 2، 5، 10، 30 ثانية وبعدها كل دقيقة). بعد 30 دقيقة بيطلع تنبيه. |
| تنبيه «انطلع من حساب زيرو» | ادخل من جديد (حساب أو QR) من نافذة إعادة الدخول، بنفس الحساب. |
| تنبيه «الاشتراك ما بيشمله» | جدّد أو رقّي من حسابك على الموقع؛ الإضافة بترجع لحالها. |
| الواجهة من برا بتقول «مسكّرة» | شغّلها من حسابك على الموقع ← البيت الذكي. وإذا في تنبيه إنها مسكّرة للأمان، اتبعه (مستخدمين محليين / trusted networks). |
| تطبيق الموبايل ما بيشتغل من برا | شغّل الواجهة من برا من حسابك على الموقع، أو حط العنوان العام بالتطبيق يدوياً. |
| بدك تشوف التفاصيل | Zero Home ← ⋮ ← Download diagnostics (المفتاح مخفي فيه). |

</div>

---

## English

**Zero Home** gives a Home Assistant that lives only on the home network a
public address on our domain (`https://<name>.home.eshtaya.ps`) and lets
Zero control it from anywhere — no port forwarding, no router settings, no
domain of your own. The integration connects **out** from the home to the
Zero relay; nothing connects into your network.

### Requirements

- Home Assistant 2024.8 or newer.
- A Zero subscription that includes Zero Home.

### Install

**HACS (custom repository):** HACS → ⋮ → Custom repositories → add this
repository as type **Integration** → find «Zero Home» → Download → restart
Home Assistant.

**Manual:** copy `custom_components/zero_home` into your
`config/custom_components/` folder and restart Home Assistant.

### Sign in (1.3.0)

Settings → Devices & services → Add integration → **Zero Home**, then either:

- **Zero username and password**, or
- **QR code:** a code appears on screen — scan it with your phone's camera or open `zero.eshtaya.ps/pair` and type
  it, then approve it in your account. The code renews itself and the screen continues on its own.

Your plan must include the smart home. Your home's address: `https://<username>.home.eshtaya.ps`.

### No settings inside Home Assistant

- The integration **only shows** the connection, the home address and the Zero account (name, subscription, days
  left), with a link to your account.
- **Every setting is in your account on Zero's site** (My account → Smart home): what Zero may see (everything /
  rooms / types / devices) and whether Home Assistant's interface opens from outside (off by default). Changes
  arrive within a minute.
- **No sign-out button.** It stays signed in until you sign it out from your account → Devices; then the link stops
  and Home Assistant asks you to sign in again (same account only).
- Upgrading from 1.2 or older: sign in once after the update; the address stays the same.
- **No configuration.yaml changes needed.** For safety: with a local-only user or trusted networks login the
  interface from outside stays closed (a repair issue explains); webhooks are not reachable through the link;
  repeated sign-in attempts are paused.

### Privacy — what goes through the link

- **Zero's control:** individual requests (read a state, call a service) for
  **exposed entities only**. No Home Assistant token is used or created;
  anything not exposed is refused inside your Home Assistant.
- **The web interface** (if enabled): your own browser/app requests, TLS up to
  our server, then through the link to your home. Sign-in is Home Assistant's
  own.
- The relay never stores or logs request bodies or headers — only connection
  events and byte counts (for the monthly limit).
- The integration sends our server: the Home Assistant version, the instance
  id, the location name and the number of exposed entities.

### Troubleshooting

| Problem | What to do |
|---|---|
| "Cannot reach Zero" while signing in | Check the internet connection and the clock. |
| The «Zero Home» sensor is off | See its `last_error` attribute. It retries by itself (1, 2, 5, 10, 30 s, then every minute); a repair issue appears after 30 min. |
| "Signed out of the Zero account" | Sign in again (account or QR) in the re-authentication dialog, with the same account. |
| "Subscription does not include it" | Renew or upgrade in your account on the site; it reconnects by itself. |
| The public address shows «closed from outside» | Turn it on in your account on the site → Smart home. If a repair issue says it is closed for safety, follow it (local-only users / trusted networks). |
| Mobile app does not work away from home | Turn on the interface from outside in your account, or enter the public address in the app. |
| Need details for support | Zero Home → ⋮ → Download diagnostics (the key is redacted). |

### Development

- `custom_components/zero_home/link/` — the tunnel (no Home Assistant imports).
- `tests/` — core tests (fake relay + fake Home Assistant): `pytest tests`.
- `tests_ha/` — Home Assistant glue tests with
  `pytest-homeassistant-custom-component`: `pytest tests_ha`.
