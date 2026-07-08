import React from 'react';
import { motion } from 'framer-motion';
import { cn } from '../../lib/cn.js';
import Sidebar from './Sidebar.jsx';
import { pageEnter } from './Motion.jsx';

/**
 * AppShell — the page chrome shared by every protected page. Composes
 * the Sidebar with a top-level animation container.
 */
export function AppShell({ children, sidebar, className }) {
  return (
    <div className={cn('flex h-screen w-screen overflow-hidden bg-background', className)}>
      {sidebar ?? <Sidebar />}
      <motion.main
        key="page"
        variants={pageEnter}
        initial="hidden"
        animate="show"
        className="flex-1 min-w-0 flex flex-col"
      >
        {children}
      </motion.main>
    </div>
  );
}

export default AppShell;
