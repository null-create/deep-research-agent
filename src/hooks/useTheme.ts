import { useState, useEffect } from 'react';

export type Theme = 'light' | 'dark';

const VALID_THEMES: Theme[] = ['light', 'dark'];

export const useTheme = () => {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const stored = localStorage.getItem('theme');
      if (stored && (VALID_THEMES as string[]).includes(stored)) {
        return stored as Theme;
      }
    } catch {
      // localStorage unavailable (private mode / restricted context) — ignore
    }
    return 'light';
  });

  useEffect(() => {
    try {
      localStorage.setItem('theme', theme);
    } catch {
      // ignore
    }
    document.documentElement.classList.toggle('dark', theme === 'dark');
  }, [theme]);

  const toggleTheme = () => {
    setTheme(prev => prev === 'light' ? 'dark' : 'light');
  };

  return { theme, toggleTheme };
};