/* Additive premium-style visual icons. Telegram buttons keep their original behavior and labels. */
(async()=>{
  const manifest=await fetch('/emoji-assets/manifest.json?v=1',{cache:'no-store'}).then(r=>r.json()).catch(()=>({}));
  const rules=[
    ['admin_panel',/لوحة تحكم|لوحة الأدمن|الإدارة|أدمن/],['settings',/إعدادات|الإعدادات|ضبط/],
    ['balance',/الرصيد|أرصدة|مبلغ|القيمة/],['wallet',/محفظة|المحفظة/],['deposit',/إيداع|شحن/],['withdraw',/سحب/],
    ['payment_success',/نجاح|تم قبول|موافَق|موافقة|مكتمل|تفعيل/],['payment_pending',/انتظار|قيد|مزامنة|جاري/],
    ['payment_failed',/فشل|رفض|غير صحيح|تعذر/],['warning',/تحذير|تنبيه|حاسم/],['error',/خطأ|إلغاء|غير مسموح/],
    ['gift',/هدية|هدايا/],['bonus',/بونص|عرض|كاش باك/],['game',/لعبة|ألعاب|iChancy/],['account',/حساب|مستخدم/],
    ['referrals',/إحالات|الإحالة/],['leaderboard',/متصدر|المتصدرين|ترتيب/],['contest',/مسابقة|مسابقات/],
    ['support',/دعم|مساعدة/],['contact',/تواصل|رسالة/],['guide',/شرح|الشروحات|دليل/],['website',/الموقع|فتح الموقع/],
    ['download',/تحميل|تنزيل/],['back',/عودة|رجوع/],['home',/الرئيسية|الرئيسية/],['close',/إغلاق/],
    ['confirm',/تأكيد|موافق|إرسال|اعتماد/],['cancel',/إلغاء/],['loading',/انتظار|جاري|تحميل/],['security',/أمان|حماية|صلاحيات/],
    ['maintenance',/صيانة|فحص|اختبار/],['brand',/Jude Robert/],['welcome',/أهلاً|مرحب/]
  ];
  const findKey=text=>{for(const [key,re] of rules)if(re.test(text))return key;return null};
  const make=(key)=>{const data=manifest[key];if(!data)return null;const span=document.createElement('span');span.className='jude-premium-icon';span.setAttribute('aria-hidden','true');const img=document.createElement('img');img.src=data.png;img.alt='';img.loading='lazy';span.append(img);return span};
  document.querySelectorAll('button,a,[role="button"],.card-title,.section-title,.badge,.greet,h1,h2,h3').forEach(el=>{if(el.closest('script,style,code,pre'))return;const key=findKey(el.textContent||'');if(!key||el.querySelector('.jude-premium-icon'))return;const icon=make(key);if(icon)el.prepend(icon)});
})();
