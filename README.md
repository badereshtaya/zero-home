# زيرو للبيت — Zero Home

<div dir="rtl">

## بالعربي

**زيرو للبيت** بيعطي Home Assistant اللي بالبيت عنوان عام على نطاقنا
(`https://<اسمك>.home.eshtaya.ps`) وبيخلي زيرو يتحكم بالبيت من أي مكان —
بدون فتح منافذ بالراوتر، بدون إعدادات، وبدون دومين خاص فيك. الإضافة بتتصل
**من البيت لبرا** بخادم الربط تبع زيرو، فما في إشي بيدخل على شبكتك من برا.

### المتطلبات

- Home Assistant إصدار 2024.6 أو أحدث.
- اشتراك بزيرو بيشمل «زيرو للبيت».

### التركيب

**عن طريق HACS (مستودع مخصص):**

1. HACS ← القائمة (⋮) ← Custom repositories.
2. حط عنوان المستودع واختار النوع **Integration**.
3. دوّر على «Zero Home» واضغط Download، وبعدها أعد تشغيل Home Assistant.

**يدوي:**

1. انسخ المجلد `custom_components/zero_home` لمجلد `config/custom_components/` عندك.
2. أعد تشغيل Home Assistant.

### التفعيل

**الطريقة الأسهل — بحساب زيرو (بدون كود):**

1. بـ Home Assistant: الإعدادات ← الأجهزة والخدمات ← إضافة تكامل ← **Zero Home**.
2. اختار **«سجّل دخول بحساب زيرو»**.
3. اكتب **نفس اسم المستخدم وكلمة السر** اللي بتدخل فيهم على زيرو. عنوان بيتك بيصير اسم المستخدم تبعك: `https://<اسم المستخدم>.home.eshtaya.ps`.

لازم باقتك تشمل البيت الذكي («المساعد الشخصي + البيت الذكي»). نفس الخطوات بتلاقيها بمعالج «اربط Home Assistant» جوّا زيرو.

**أو برمز تفعيل:** من «حسابي ← البيت الذكي ← زيرو للبيت» خذ رمز `ZH-XXXX-XXXX-XXXX` (لمرة وحدة، صالح 24 ساعة)، وبالخطوة 2 اختار «معي رمز تفعيل».

(بوضع «الخيارات المتقدمة» بتقدر تغيّر عنوان Zero Admin — اتركه مثل ما هو إلا إذا الدعم قلّك.)

### المعالج (نسخة 1.2.0)

الإعداد صار معالج من 4 خطوات: ترحيب ← تسجيل الدخول بزيرو ← **شو بيشوف زيرو** (كل إشي، أو بس غرف، أو بس أنواع أجهزة، أو اختيار دقيق) ← **واجهة Home Assistant من برا؟** ← ملخّص بعنوان بيتك.
لتعديله بعدين: الإعدادات ← الأجهزة والخدمات ← Zero Home ← «تكوين» — نفس الخطوات وآخرها حالة الاتصال.

- **واجهة Home Assistant من برا:** **مطفية افتراضياً** — بتشغّلها إنت إذا بدك تفتح شاشات Home Assistant على عنوانك العام (وبتبين داخل تبويب «Home Assistant» بزيرو). التحكم من زيرو بالصوت شغّال بكل الأحوال.
- **العنوان الخارجي:** لما تشغّل الواجهة من برا، الإضافة بتحط عنوانك العام كـ `external_url` (إذا كان عندك عنوان ثاني، ما بنغيّره إلا بعد ما توافق).

### بدون أي تعديل على configuration.yaml

نسخ Home Assistant الجديدة ما بتقبل `trusted_proxies` بالكونفج، فالإضافة صارت تشتغل بـ«الوضع المباشر»: Home Assistant بيشوف الزوار جايين من الإضافة نفسها. عشان هيك، للأمان:

- إذا في مستخدم معلّم عليه «Can only log in from the local network»، أو تسجيل دخول «trusted networks» مفعّل، الواجهة من برا بتضل مسكّرة وبيطلعلك تنبيه (Repairs) بيشرح شو تعمل.
- الـ webhooks ما بتنفتح من برا عن طريق الربط.
- محاولات تسجيل الدخول الكتير من نفس الزائر بتنوقف مؤقتاً (الإضافة بتعدّها حسب عنوانه الحقيقي).

(إذا نسختك لسا بتقبل `http: use_x_forwarded_for: true` + `trusted_proxies: [127.0.0.1, ::1]`، الإضافة بتستعملهم لحالها.)

### الخصوصية — شو بيمر عن طريق الربط

- **تحكم زيرو:** طلبات محددة (قراءة حالة، تشغيل خدمة) على الأجهزة المسموحة **بس**. ما بنستعمل ولا بنعمل أي token لـ Home Assistant؛ أي جهاز مش مسموح بيترفض.
- **الواجهة من برا** (إذا مفعّلة): طلباتك أنت من المتصفح أو التطبيق بتمر مشفّرة لحد خادمنا وبعدها للبيت. تسجيل الدخول هو تسجيل دخول Home Assistant نفسه.
- خادم الربط **ما بيخزّن ولا بيسجّل** محتوى الطلبات أو الـ headers؛ بيسجّل بس أوقات الاتصال وعدد البايتات (للحد الشهري).
- الإضافة بتبعت لخادمنا: إصدار Home Assistant، معرّف النسخة، اسم البيت، وعدد الأجهزة المسموحة.

### حل المشاكل

| المشكلة | الحل |
|---|---|
| «ما قدرنا نوصل لزيرو» وقت التفعيل | تأكد إنه Home Assistant متصل بالإنترنت وإنه الوقت مضبوط. |
| الحساس «Zero Home» مطفي | شوف سمة `last_error` فيه. الإضافة بتحاول لحالها (1، 2، 5، 10، 30 ثانية وبعدها كل دقيقة). بعد 30 دقيقة بيطلع تنبيه. |
| تنبيه «الربط توقّف» أو «الاشتراك ما بيشمله» | سجّل دخول من جديد بحساب زيرو (أو رمز تفعيل جديد) بنافذة إعادة المصادقة. |
| الواجهة من برا بتقول «مسكّرة» | شغّل «افتح واجهة Home Assistant من برّا» من «تكوين». وإذا في تنبيه إنها مسكّرة للأمان، اتبعه (مستخدمين محليين / trusted networks). |
| تطبيق الموبايل ما بيشتغل من برا | تأكد إنه خيار العنوان الخارجي مفعّل، أو حط العنوان العام بالتطبيق يدوياً. |
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

- Home Assistant 2024.6 or newer.
- A Zero subscription that includes Zero Home.

### Install

**HACS (custom repository):** HACS → ⋮ → Custom repositories → add this
repository as type **Integration** → find «Zero Home» → Download → restart
Home Assistant.

**Manual:** copy `custom_components/zero_home` into your
`config/custom_components/` folder and restart Home Assistant.

### Activate

**The easy way — with your Zero account (no code):**

1. In Home Assistant: *Settings → Devices & services → Add integration → Zero Home*.
2. Choose **"Sign in with my Zero account"**.
3. Enter the **same username and password** you sign in to Zero with. The home's address becomes your
   username: `https://<username>.home.eshtaya.ps`.

Your plan must include the smart home. Zero's own "Link Home Assistant" wizard shows the same steps.

**Or with an activation code:** in Zero open *My account → Smart home → Zero Home* and get a code
`ZH-XXXX-XXXX-XXXX` (single use, valid 24 h); at step 2 choose "I have an activation code".

With *Advanced mode* on in your profile you can change the Zero Admin API
address; leave it unless Zero support tells you otherwise.

### The wizard (1.2.0)

Set-up is a 4-step wizard: welcome → sign in to Zero → **what Zero may see** (everything, some rooms,
some device types, or exactly chosen) → **Home Assistant's interface from outside?** → summary with your
home's address. *Configure* later shows the same steps and the live connection status.

- **Web interface from outside: off by default.** Turn it on only if you want Home Assistant's own screens
  on your public address (and in Zero's «Home Assistant» tab). Zero's voice control works either way.
- **External URL:** when the interface is on, the public address becomes `external_url` (an existing
  different one is replaced only after you confirm).

### No configuration.yaml changes

Newer Home Assistant versions no longer take `trusted_proxies` from configuration.yaml, so the integration
works in *direct mode*: Home Assistant sees visitors as coming from the integration itself. For safety:

- if any user has «Can only log in from the local network», or the «trusted networks» login is enabled,
  the interface from outside stays closed and a repair issue explains what to change;
- webhooks are not reachable through the link;
- repeated sign-in attempts from one visitor are blocked for a while (counted by their real address).

(If your version still accepts `http: use_x_forwarded_for: true` + `trusted_proxies: [127.0.0.1, ::1]`,
the integration uses it automatically.)

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
| "Cannot reach Zero" while activating | Check the internet connection and the clock. |
| The «Zero Home» sensor is off | See its `last_error` attribute. It retries by itself (1, 2, 5, 10, 30 s, then every minute); a repair issue appears after 30 min. |
| "The link was stopped" / "subscription does not include it" | Sign in again with your Zero account (or a new activation code) in the re-authentication dialog. |
| The public address shows «closed from outside» | Turn on «Open Home Assistant's interface from outside» in *Configure*. If a repair issue says it is closed for safety, follow it (local-only users / trusted networks). |
| Mobile app does not work away from home | Enable the external URL option, or enter the public address in the app. |
| Need details for support | Zero Home → ⋮ → Download diagnostics (the key is redacted). |

### Development

- `custom_components/zero_home/link/` — the tunnel (no Home Assistant imports).
- `tests/` — core tests (fake relay + fake Home Assistant): `pytest tests`.
- `tests_ha/` — Home Assistant glue tests with
  `pytest-homeassistant-custom-component`: `pytest tests_ha`.
