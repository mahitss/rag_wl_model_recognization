/**
 * M77: Internationalization system for WonderLearn.
 *
 * Provides:
 *   * Centralized translation resources (English + Hindi).
 *   * Language selection with localStorage persistence.
 *   * Locale-aware date/time, number, and percentage formatting.
 *   * Pluralization support via Intl.PluralRules.
 *   * Missing-translation detection with English fallback.
 *   * Devanagari font fallback stack for Hindi rendering.
 *
 * Usage:
 *   import { t, formatDate, formatNumber, LANGUAGES } from "./i18n";
 *   <h1>{t("app.title")}</h1>
 */

export type LanguageCode = "en" | "hi";

export const LANGUAGES: Record<LanguageCode, { name: string; nativeName: string; dir: "ltr" | "rtl" }> = {
  en: { name: "English", nativeName: "English", dir: "ltr" },
  hi: { name: "Hindi", nativeName: "हिन्दी", dir: "ltr" },
};

export const DEFAULT_LANGUAGE: LanguageCode = "en";

type TranslationDictionary = Record<string, string | Record<string, any> | ((params?: Record<string, string | number>) => string)>;

// ---------------------------------------------------------------------------
// Translation dictionaries.
// Keys use dot-notation categories: common, nav, pages, components, errors, ai.
// ---------------------------------------------------------------------------

const translations: Record<LanguageCode, TranslationDictionary> = {
  en: {
    common: {
      appTitle: "WonderLearn",
      appShort: "WL",
      save: "Save",
      cancel: "Cancel",
      confirm: "Confirm",
      delete: "Delete",
      edit: "Edit",
      close: "Close",
      back: "Back",
      next: "Next",
      previous: "Previous",
      search: "Search",
      searchPlaceholder: "Search your study materials…",
      loading: "Loading…",
      error: "Error",
      success: "Success",
      tryAgain: "Try again",
      language: "Language",
      selectLanguage: "Select language",
      theme: "Theme",
      settings: "Settings",
      help: "Help",
      logout: "Logout",
      login: "Log in",
      register: "Register",
      email: "Email",
      password: "Password",
      username: "Username",
      noResults: "No results found.",
      resultsFound: (params: Record<string, string | number>) => `${params.count} result${params.count !== 1 ? "s" : ""} found.`,
      optional: "Optional",
      required: "Required",
      minutes: (n: number) => `${n} min${n !== 1 ? "s" : ""}`,
    },
    nav: {
      dashboard: "Dashboard",
      documents: "Documents",
      quizzes: "Quizzes",
      revision: "Revision",
      classrooms: "Classrooms",
      communicate: "Communicate",
      progress: "Progress",
      announcements: "Announcements",
      contentStudio: "Content Studio",
      modelObservability: "Model Observability",
    },
    pages: {
      home: "Home",
      classroom: "Classroom",
      classroomTitle: "Classroom: {{title}}",
      noClassrooms: "No classrooms available.",
      noClassroomsDesc: "You are not a participant of any classroom sessions yet. Classrooms are created by educators.",
      selectClassroom: "Select a classroom",
      learnerProgress: "Learner Progress Portal",
      learnerProgressDesc: "Observed progress from quiz attempts, revision activity, and study sessions.",
    },
    components: {
      cameraPanel: "Camera alignment prototype",
      cameraOn: "Camera on · stays on this device",
      cameraOff: "Camera off",
      send: "Send",
      post: "Post",
      postReply: "Post reply",
      markAsRead: "Mark as read",
      unread: "Unread",
      direct: "Direct",
      classroom: "Classroom",
      announcement: "Announcement",
      feedback: "Feedback",
      progressUpdate: "Progress update",
      validateContent: "Validate content",
      regenerate: "Regenerate",
      markVerified: "Mark verified",
      markNeedsReview: "Mark needs review",
      markInsufficientEvidence: "Mark insufficient evidence",
      remembered: "Remembered",
      partially: "Partially",
      forgotten: "Forgotten",
    },
    errors: {
      loginFailed: "Login failed. Please check your credentials.",
      networkError: "Network error. Please try again later.",
      notFound: "Not found.",
      unauthorized: "You are not authorized to perform this action.",
      forbidden: "You are not a member of this institution.",
      serverError: "Server error. Please try again later.",
      unknown: "An unexpected error occurred.",
    },
    ai: {
      tutorTitle: "AI Tutor",
      tutorPrompt: "Ask a question, start a quiz, or ask for study recommendations…",
      tutorSending: "Sending…",
      tutorNoMessages: "No messages yet. Send a message to start chatting.",
      newConversation: "New conversation",
      conversations: "Conversations",
      noConversations: "No conversations yet. Send a message to start a conversation.",
      generating: "Generating…",
      evaluating: "Evaluating…",
      validateAgainstSources: "Validate content against sources",
      citationValidity: "Citations valid",
      schemaValidity: "Schema valid",
      unsupportedClaims: "Unsupported claims",
      evidenceCoverage: "Evidence coverage",
    },
  },

  hi: {
    common: {
      appTitle: "वंडरलर्न",
      appShort: "डब्ल्यूएल",
      save: "सहेजें",
      cancel: "रद्द करें",
      confirm: "पुष्टि करें",
      delete: "हटाएं",
      edit: "संपादित करें",
      close: "बंद करें",
      back: "वापस",
      next: "अगला",
      previous: "पिछला",
      search: "खोजें",
      searchPlaceholder: "अपनी पढ़ाई की सामग्री खोजें…",
      loading: "लोड हो रहा है…",
      error: "त्रुटि",
      success: "सफलता",
      tryAgain: "फिर से प्रयास करें",
      language: "भाषा",
      selectLanguage: "भाषा चुनें",
      theme: "थीम",
      settings: "सेटिंग्ज़",
      help: "सहायता",
      logout: "लॉगआउट",
      login: "लॉग इन करें",
      register: "पंजीकरण",
      email: "ईमेल",
      password: "पासवर्ड",
      username: "उपयोगकर्ता नाम",
      noResults: "कोई परिणाम नहीं मिला।",
      resultsFound: (params: Record<string, string | number>) => `${params.count} परिणाम${params.count !== 1 ? " मिले" : " मिला"}।`,
      optional: "वैकल्पिक",
      required: "आवश्यक",
      minutes: (n: number) => `${n} मिनट`,
    },
    nav: {
      dashboard: "डैशबोर्ड",
      documents: "दस्तावेज़",
      quizzes: "क्विज़",
      revision: "समीक्षा",
      classrooms: "कक्षाएं",
      communicate: "संचार",
      progress: "प्रगति",
      announcements: "घोषणाएं",
      contentStudio: "सामग्री स्टूडियो",
      modelObservability: "मॉडल वेधशाला",
    },
    pages: {
      home: "होम",
      classroom: "कक्षा",
      classroomTitle: "कक्षा: {{title}}",
      noClassrooms: "कोई कक्षा उपलब्ध नहीं।",
      noClassroomsDesc: "आप किसी कक्षा सत्र के भागीदार नहीं हैं। कक्षाएं शिक्षकों द्वारा बनाई जाती हैं।",
      selectClassroom: "कक्षा चुनें",
      learnerProgress: "शिक्षार्थी प्रगति पोर्टल",
      learnerProgressDesc: "क्विज़ प्रयास, समीक्षा गतिविधि और अध्ययन सत्रों से देखा गया التقدم।",
    },
    components: {
      cameraPanel: "कैमरा संरेखण प्रोटोटाइप",
      cameraOn: "कैमरा चालू · केवल इस डिवाइस पर रहता है",
      cameraOff: "कैमरा बंद",
      send: "भेजें",
      post: "पोस्ट करें",
      postReply: "उत्तर पोस्ट करें",
      markAsRead: "पढ़ा हुआ चिह्नित करें",
      unread: "अपढ़ा",
      direct: "प्रत्यक्ष",
      classroom: "कक्षा",
      announcement: "घोषणा",
      feedback: "प्रतिक्रिया",
      progressUpdate: "प्रगति अद्यतन",
      validateContent: "सामग्री सत्यापित करें",
      regenerate: "पुनर्जनरेट करें",
      markVerified: "सत्यापित चिह्नित करें",
      markNeedsReview: "समीक्षा की आवश्यकता चिह्नित करें",
      markInsufficientEvidence: "अपर्याप्त साक्ष्य चिह्नित करें",
      remembered: "याद रखा",
      partially: "आंशिक रूप से",
      forgotten: "भूल गए",
    },
    errors: {
      loginFailed: "लॉगिन विफल रहा। कृपया अपनी क्रेडेंशियल्स जांचें।",
      networkError: "नेटवर्क त्रुटि। कृपया बाद में पुनः प्रयास करें।",
      notFound: "मिल नहीं रहा।",
      unauthorized: "आप इस कार्य को करने के लिए अधिकृत नहीं हैं।",
      forbidden: "आप इस संस्थान के सदस्य नहीं हैं।",
      serverError: "सर्वर त्रुटि। कृपया बाद में पुनः प्रयास करें।",
      unknown: "एक अप्रत्याशित त्रुटि हुई।",
    },
    ai: {
      tutorTitle: "एआई ट्यूटर",
      tutorPrompt: "अपनी पढ़ाई की सामग्री के बारे में प्रश्न पूछें, क्विज़ शुरू करें, या अध्ययन सिफारिशें मांगें…",
      tutorSending: "भेजा जा रहा है…",
      tutorNoMessages: "अभी तक कोई संदेश नहीं। चैट शुरू करने के लिए एक संदेश भेजें।",
      newConversation: "नई बातचीत",
      conversations: "बातचीत",
      noConversations: "अभी तक कोई बातचीत नहीं। बातचीत शुरू करने के लिए एक संदेश भेजें।",
      generating: "जनरेट हो रहा है…",
      evaluating: "मूल्यांकन हो रहा है…",
      validateAgainstSources: "स्रोतों के विरुद्ध सामग्री सत्यापित करें",
      citationValidity: "उद्धरण वैधता",
      schemaValidity: "स्कीमा वैधता",
      unsupportedClaims: "असमर्थित दावे",
      evidenceCoverage: "साक्ष्य कवरेज",
    },
  },
};

// ---------------------------------------------------------------------------
// Formatting helpers (locale-aware)
// ---------------------------------------------------------------------------

const DATE_OPTIONS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
};
const DATE_TIME_OPTIONS: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};
const NUMBER_OPTIONS: Intl.NumberFormatOptions = {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
};
const PERCENT_OPTIONS: Intl.NumberFormatOptions = {
  style: "percent",
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
};

export function formatDate(isoString: string, lang: LanguageCode = DEFAULT_LANGUAGE): string {
  return new Intl.DateTimeFormat(lang, DATE_OPTIONS).format(new Date(isoString));
}

export function formatDateTime(isoString: string, lang: LanguageCode = DEFAULT_LANGUAGE): string {
  return new Intl.DateTimeFormat(lang, DATE_TIME_OPTIONS).format(new Date(isoString));
}

export function formatNumber(value: number, lang: LanguageCode = DEFAULT_LANGUAGE): string {
  return new Intl.NumberFormat(lang, NUMBER_OPTIONS).format(value);
}

export function formatPercent(value: number, lang: LanguageCode = DEFAULT_LANGUAGE): string {
  return new Intl.NumberFormat(lang, PERCENT_OPTIONS).format(value / 100);
}

export function formatRelativeTime(
  value: number,
  unit: Intl.RelativeTimeFormatUnit,
  lang: LanguageCode = DEFAULT_LANGUAGE,
): string {
  return new Intl.RelativeTimeFormat(lang, { numeric: "auto" }).format(value, unit);
}

export function pluralize(
  count: number,
  one: string,
  other: string,
  lang: LanguageCode = DEFAULT_LANGUAGE,
): string {
  const rule = new Intl.PluralRules(lang).select(count);
  return rule === "one" ? one : other;
}

// ---------------------------------------------------------------------------
// Translation lookup with dot-notation and parameter substitution
// ---------------------------------------------------------------------------

function resolveKey(dict: TranslationDictionary | undefined, key: string | undefined): string | ((params?: Record<string, string | number>) => string) | undefined {
  if (!dict || !key) return undefined;
  const parts = key.split(".");
  let current: any = dict;
  for (const part of parts) {
    if (current === undefined || typeof current !== "object") return undefined;
    current = current[part];
  }
  return typeof current === "string" || typeof current === "function" ? current : undefined;
}

function substituteParams(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{\{(\w+)\}\}/g, (_match, key) => {
    const value = params[key];
    return value === undefined || value === null ? "{{" + key + "}}" : String(value);
  });
}

export function t(
  key: string,
  params?: Record<string, string | number>,
  lang: LanguageCode = DEFAULT_LANGUAGE,
): string {
  if (!key) return key;
  const dict = translations[lang as LanguageCode];
  if (!dict) return key;
  const resolved = resolveKey(dict, key);
  const fallback = resolveKey(translations[DEFAULT_LANGUAGE], key);
  const template = resolved ?? fallback ?? key;
  if (typeof template === "function") {
    return (template as (params: Record<string, string | number>) => string)(params ?? {});
  }
  return substituteParams(String(template), params);
}

export { LanguageProvider, useLanguage } from "./LanguageProvider";

export function getTranslation(
  key: string,
  lang: LanguageCode = DEFAULT_LANGUAGE,
): string | undefined {
  const dict = translations[lang];
  const resolved = resolveKey(dict, key);
  const fallback = resolveKey(translations[DEFAULT_LANGUAGE], key);
  const result = resolved ?? fallback;
  if (typeof result === "function") {
    return result({});
  }
  return result;
}
