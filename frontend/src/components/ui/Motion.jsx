/**
 * Shared Framer Motion variants. Centralised so we can tune the
 * feel of the whole app from one place.
 */
export const fadeUp = {
  hidden: { opacity: 0, y: 6 },
  show:   { opacity: 1, y: 0, transition: { duration: 0.24, ease: [0.16, 1, 0.3, 1] } },
  exit:   { opacity: 0, y: 6, transition: { duration: 0.16, ease: 'easeOut' } },
};

export const fadeIn = {
  hidden: { opacity: 0 },
  show:   { opacity: 1, transition: { duration: 0.2, ease: 'easeOut' } },
  exit:   { opacity: 0, transition: { duration: 0.15, ease: 'easeOut' } },
};

export const scaleIn = {
  hidden: { opacity: 0, scale: 0.98 },
  show:   { opacity: 1, scale: 1, transition: { duration: 0.18, ease: [0.16, 1, 0.3, 1] } },
  exit:   { opacity: 0, scale: 0.98, transition: { duration: 0.14, ease: 'easeOut' } },
};

export const slideInRight = {
  hidden: { opacity: 0, x: 16 },
  show:   { opacity: 1, x: 0, transition: { duration: 0.24, ease: [0.16, 1, 0.3, 1] } },
  exit:   { opacity: 0, x: 16, transition: { duration: 0.18, ease: 'easeOut' } },
};

export const pageEnter = {
  hidden: { opacity: 0, y: 8 },
  show:   { opacity: 1, y: 0, transition: { duration: 0.32, ease: [0.16, 1, 0.3, 1] } },
};

/** Common AnimatePresence transition config. */
export const presence = { mode: 'wait' };
