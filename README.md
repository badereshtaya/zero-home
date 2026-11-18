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

### الخيارات

من الإعدادات ← الأجهزة والخدمات ← Zero Home ← «تكوين»:

- **شو زيرو بيشوف ويتحكم:** كل الأجهزة، أو بس اللي بتختارها — غرف، أنواع (light، switch…)، وسوم، أو قائمة أجهزة. الجهاز بيبين إذا طابق **أي** وحدة من اختياراتك.
- **واجهة Home Assistant من برا:** (مفعّل افتراضياً) بتفتح الواجهة على عنوانك العام وبتبين داخل تبويب «Home Assistant» بزيرو.
- **العنوان الخارجي:** (مفعّل افتراضياً) الإضافة بتحط عنوانك العام كـ `external_url` عشان تطبيقات Home Assistant الرسمية بالموبايل تشتغل من أي مكان. إذا كان عندك عنوان خارجي ثاني، ما بنغيّره إلا بعد ما توافق.

### سطرين لازم تضيفهم (للواجهة من برا)

عشان Home Assistant يعرف العنوان الحقيقي لمين بيفتح الواجهة من برا (وتضل حماية الحظر وتسجيل الدخول المحلي شغّالة صح)، ضيف على `configuration.yaml` وأعد التشغيل:

```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 127.0.0.1
    - ::1
```

إذا ناقصين، الإضافة بتعرضلك تنبيه بالإصلاحات (Repairs) فيه نفس الأسطر. التحكم بالصوت وتحكم زيرو بالأجهزة بيشتغلوا بدونهم.

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
| الواجهة من برا بتعطي صفحة شرح | ضيف السطرين فوق وأعد التشغيل. |
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

### Options

- **What Zero may see and control:** every entity, or only the chosen
  areas, domains, labels and/or entities (an entity is exposed when it matches
  **any** of them).
- **Web interface through the link** (default on): the Home Assistant UI on
  your public address and inside Zero's «Home Assistant» tab.
- **External URL** (default on): sets Home Assistant's `external_url` to the
  public address so the official mobile apps work from anywhere. If another
  external URL is already set, it is changed only after you confirm.

### The two lines for the web interface

```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 127.0.0.1
    - ::1
```

Without them Home Assistant cannot see the real address of outside visitors
(IP bans and local-only users would not work right), so the relay shows an
explanation page instead of the UI. A repair issue shows the same lines.
Voice control and Zero's control of devices work without them.

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
| The public address shows an explanation page | Add the two lines above and restart. |
| Mobile app does not work away from home | Enable the external URL option, or enter the public address in the app. |
| Need details for support | Zero Home → ⋮ → Download diagnostics (the key is redacted). |

### Development

- `custom_components/zero_home/link/` — the tunnel (no Home Assistant imports).
- `tests/` — core tests (fake relay + fake Home Assistant): `pytest tests`.
- `tests_ha/` — Home Assistant glue tests with
  `pytest-homeassistant-custom-component`: `pytest tests_ha`.
