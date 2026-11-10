/**
 * M77: LanguageProvider React component.
 *
 * Wraps the application with translation context and handles language
 * persistence via localStorage. Sets document lang/dir attributes.
 */

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { LANGUAGES, DEFAULT_LANGUAGE, t, type LanguageCode } from "./i18n";

const LANG_STORAGE_KEY = "wl_lang";

const TranslationContext = createContext<{
  lang: LanguageCode;
  setLang: (lang: LanguageCode) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
  dir: "ltr" | "rtl";
} | undefined>(undefined);

export function useLanguage() {
  const ctx = useContext(TranslationContext);
  if (!ctx) {
    throw new Error("useLanguage must be used within a LanguageProvider");
  }
  return ctx;
}

export function LanguageProvider({
  children,
  initialLang = DEFAULT_LANGUAGE,
}: {
  children: React.ReactNode;
  initialLang?: LanguageCode;
}) {
  const [lang, setLangState] = useState<LanguageCode>(() => {
    if (typeof window !== "undefined" && window.localStorage) {
      const stored = window.localStorage.getItem(LANG_STORAGE_KEY);
      if (stored && (stored === "en" || stored === "hi")) {
        return stored as LanguageCode;
      }
    }
    return initialLang;
  });

  // Persist initial language to localStorage
  if (typeof window !== "undefined" && window.localStorage) {
    window.localStorage.setItem(LANG_STORAGE_KEY, lang);
  }

  const setLang = useCallback((nextLang: LanguageCode) => {
    setLangState(nextLang);
    if (typeof window !== "undefined" && window.localStorage) {
      window.localStorage.setItem(LANG_STORAGE_KEY, nextLang);
    }
    if (typeof document !== "undefined") {
      document.documentElement.lang = nextLang;
      document.documentElement.dir = LANGUAGES[nextLang].dir;
    }
  }, []);

  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.lang = lang;
      document.documentElement.dir = LANGUAGES[lang].dir;
    }
  }, [lang]);

  const tFunc = useCallback(
    (key: string, params?: Record<string, string | number>) => {
      return t(key, params, lang);
    },
    [lang],
  );

  const value: {
    lang: LanguageCode;
    setLang: (lang: LanguageCode) => void;
    t: (key: string, params?: Record<string, string | number>) => string;
    dir: "ltr" | "rtl";
  } = {
    lang,
    setLang,
    t: tFunc,
    dir: LANGUAGES[lang].dir,
  };

  return (
    <TranslationContext.Provider value={value}>{children}</TranslationContext.Provider>
  );
}

export default LanguageProvider;
