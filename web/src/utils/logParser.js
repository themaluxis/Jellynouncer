/**
 * Log Parser for Jellynouncer Log Format
 * 
 * Parses logs in the format:
 * [2025-08-17 11:26:15 UTC][INFO][jellynouncer.webhook] Message text
 * 
 * Extracts:
 * - Timestamp in brackets
 * - Log level in brackets (colored based on severity)
 * - Component name in brackets (with dot notation support)
 * - Message text
 */

// Log level colors matching the severity
export const LOG_LEVEL_COLORS = {
  DEBUG: {
    color: '#6b7280', // gray-500
    bgColor: 'rgba(107, 114, 128, 0.1)',
    label: 'DEBUG'
  },
  INFO: {
    color: '#3b82f6', // blue-500
    bgColor: 'rgba(59, 130, 246, 0.1)',
    label: 'INFO'
  },
  WARNING: {
    color: '#eab308', // yellow-500
    bgColor: 'rgba(234, 179, 8, 0.1)',
    label: 'WARNING'
  },
  WARN: {
    color: '#eab308', // yellow-500
    bgColor: 'rgba(234, 179, 8, 0.1)',
    label: 'WARN'
  },
  ERROR: {
    color: '#ef4444', // red-500
    bgColor: 'rgba(239, 68, 68, 0.1)',
    label: 'ERROR'
  },
  CRITICAL: {
    color: '#dc2626', // red-600
    bgColor: 'rgba(220, 38, 38, 0.1)',
    label: 'CRITICAL'
  },
  FATAL: {
    color: '#991b1b', // red-800
    bgColor: 'rgba(153, 27, 27, 0.1)',
    label: 'FATAL'
  }
};

/**
 * Parse a single log line
 * 
 * @param {string} logLine - The raw log line
 * @returns {object} Parsed log object with timestamp, level, component, and message
 */
export function parseLogLine(logLine) {
  // Regex to match: [timestamp][level][component] message
  // Updated to handle both UTC and non-UTC timestamps
  const logRegex = /^\[([^\]]+)\]\[([^\]]+)\]\[([^\]]+)\]\s*(.*)$/;
  
  const match = logLine.match(logRegex);
  
  if (!match) {
    // If the line doesn't match the expected format, treat it as a continuation
    // of the previous log message or as plain text
    return {
      type: 'continuation',
      text: logLine,
      raw: logLine
    };
  }
  
  const [, timestamp, level, component, message] = match;
  
  // Get level styling
  const levelUpper = level.toUpperCase();
  const levelStyle = LOG_LEVEL_COLORS[levelUpper] || {
    color: '#9ca3af', // gray-400 for unknown levels
    bgColor: 'rgba(156, 163, 175, 0.1)',
    label: levelUpper
  };
  
  return {
    type: 'log',
    timestamp: timestamp.trim(),
    level: levelUpper,
    levelStyle,
    component: component.trim(),
    message: message.trim(),
    raw: logLine
  };
}

/**
 * Parse multiple log lines
 * 
 * @param {string} logText - The raw log text with multiple lines
 * @returns {array} Array of parsed log objects
 */
export function parseLogText(logText) {
  if (!logText) return [];
  
  const lines = logText.split('\n');
  const parsedLogs = [];
  let currentLog = null;
  
  lines.forEach(line => {
    if (!line.trim()) {
      // Empty line
      if (currentLog && currentLog.type === 'log') {
        // Add empty line to current log's message for multi-line logs
        currentLog.message += '\n';
      }
      return;
    }
    
    const parsed = parseLogLine(line);
    
    if (parsed.type === 'log') {
      // New log entry
      if (currentLog) {
        parsedLogs.push(currentLog);
      }
      currentLog = parsed;
    } else if (parsed.type === 'continuation' && currentLog) {
      // Continuation of previous log
      currentLog.message += '\n' + parsed.text;
      currentLog.raw += '\n' + parsed.raw;
    } else {
      // Standalone line (no previous log to attach to)
      parsedLogs.push({
        type: 'text',
        text: line,
        raw: line
      });
    }
  });
  
  // Don't forget the last log
  if (currentLog) {
    parsedLogs.push(currentLog);
  }
  
  return parsedLogs;
}

/**
 * Filter logs based on criteria
 * 
 * @param {array} logs - Array of parsed log objects
 * @param {object} filters - Filter criteria
 * @returns {array} Filtered log array
 */
export function filterLogs(logs, filters = {}) {
  const { level, component, search, startTime, endTime } = filters;
  
  return logs.filter(log => {
    if (log.type !== 'log') return true; // Always include non-log lines
    
    // Filter by level
    if (level && log.level !== level.toUpperCase()) {
      return false;
    }
    
    // Filter by component (supports partial matching)
    if (component && !log.component.toLowerCase().includes(component.toLowerCase())) {
      return false;
    }
    
    // Filter by search text (searches in message and component)
    if (search) {
      const searchLower = search.toLowerCase();
      const inMessage = log.message.toLowerCase().includes(searchLower);
      const inComponent = log.component.toLowerCase().includes(searchLower);
      if (!inMessage && !inComponent) {
        return false;
      }
    }
    
    // Filter by time range
    if (startTime || endTime) {
      try {
        const logTime = new Date(log.timestamp.replace(' UTC', 'Z'));
        if (startTime && logTime < new Date(startTime)) {
          return false;
        }
        if (endTime && logTime > new Date(endTime)) {
          return false;
        }
      } catch (e) {
        // If timestamp parsing fails, include the log
        console.warn('Failed to parse timestamp:', log.timestamp);
      }
    }
    
    return true;
  });
}

/**
 * Get statistics from parsed logs
 * 
 * @param {array} logs - Array of parsed log objects
 * @returns {object} Statistics object
 */
export function getLogStatistics(logs) {
  const stats = {
    total: 0,
    byLevel: {},
    byComponent: {},
    errorCount: 0,
    warningCount: 0,
    recentErrors: []
  };
  
  logs.forEach(log => {
    if (log.type !== 'log') return;
    
    stats.total++;
    
    // Count by level
    stats.byLevel[log.level] = (stats.byLevel[log.level] || 0) + 1;
    
    // Count by component
    const mainComponent = log.component.split('.')[0];
    stats.byComponent[mainComponent] = (stats.byComponent[mainComponent] || 0) + 1;
    
    // Track errors and warnings
    if (log.level === 'ERROR' || log.level === 'CRITICAL' || log.level === 'FATAL') {
      stats.errorCount++;
      if (stats.recentErrors.length < 10) {
        stats.recentErrors.push({
          timestamp: log.timestamp,
          component: log.component,
          message: log.message.substring(0, 100)
        });
      }
    } else if (log.level === 'WARNING' || log.level === 'WARN') {
      stats.warningCount++;
    }
  });
  
  return stats;
}

/**
 * Format log for display
 * 
 * @param {object} log - Parsed log object
 * @param {object} options - Display options
 * @returns {object} Formatted log for React component
 */
export function formatLogForDisplay(log, options = {}) {
  const { showTimestamp = true, showComponent = true, highlightSearch = null } = options;
  
  if (log.type !== 'log') {
    return {
      type: log.type,
      content: log.text,
      className: 'text-gray-500 font-mono text-sm'
    };
  }
  
  // Highlight search terms if provided
  let displayMessage = log.message;
  if (highlightSearch) {
    const regex = new RegExp(`(${highlightSearch})`, 'gi');
    displayMessage = log.message.replace(regex, '<mark class="bg-yellow-300 text-black">$1</mark>');
  }
  
  return {
    type: 'log',
    timestamp: showTimestamp ? log.timestamp : null,
    level: {
      text: log.level,
      color: log.levelStyle.color,
      bgColor: log.levelStyle.bgColor,
      className: `px-2 py-0.5 rounded text-xs font-bold`
    },
    component: showComponent ? {
      text: log.component,
      className: 'text-purple-400 font-mono'
    } : null,
    message: {
      html: displayMessage,
      className: 'font-mono text-sm'
    },
    rowClassName: `hover:bg-gray-800/50 transition-colors ${
      log.level === 'ERROR' || log.level === 'CRITICAL' ? 'border-l-4 border-red-500' : ''
    }`
  };
}

/**
 * Export logs as different formats
 * 
 * @param {array} logs - Array of parsed log objects
 * @param {string} format - Export format ('txt', 'json', 'csv')
 * @returns {string} Formatted export string
 */
export function exportLogs(logs, format = 'txt') {
  switch (format) {
    case 'json':
      return JSON.stringify(logs.filter(l => l.type === 'log').map(log => ({
        timestamp: log.timestamp,
        level: log.level,
        component: log.component,
        message: log.message
      })), null, 2);
    
    case 'csv':
      const headers = 'Timestamp,Level,Component,Message';
      const rows = logs.filter(l => l.type === 'log').map(log => 
        `"${log.timestamp}","${log.level}","${log.component}","${log.message.replace(/"/g, '""')}"`
      );
      return [headers, ...rows].join('\n');
    
    case 'txt':
    default:
      return logs.map(log => log.raw).join('\n');
  }
}

/**
 * Create a virtual scrolling window for large log files
 * 
 * @param {array} logs - Array of parsed log objects
 * @param {number} startIndex - Start index for the window
 * @param {number} windowSize - Number of logs to include in window
 * @returns {array} Windowed log array
 */
export function createLogWindow(logs, startIndex, windowSize = 100) {
  return logs.slice(startIndex, startIndex + windowSize);
}

/**
 * Search logs with context
 * 
 * @param {array} logs - Array of parsed log objects
 * @param {string} searchTerm - Search term
 * @param {number} contextLines - Number of lines before/after to include
 * @returns {array} Logs with search results and context
 */
export function searchLogsWithContext(logs, searchTerm, contextLines = 2) {
  const results = [];
  const searchLower = searchTerm.toLowerCase();
  
  logs.forEach((log, index) => {
    if (log.type !== 'log') return;
    
    const inMessage = log.message.toLowerCase().includes(searchLower);
    const inComponent = log.component.toLowerCase().includes(searchLower);
    
    if (inMessage || inComponent) {
      // Include context lines
      const start = Math.max(0, index - contextLines);
      const end = Math.min(logs.length - 1, index + contextLines);
      
      for (let i = start; i <= end; i++) {
        const contextLog = { ...logs[i] };
        contextLog.isMatch = i === index;
        contextLog.isContext = i !== index;
        results.push(contextLog);
      }
    }
  });
  
  // Remove duplicates while preserving order
  const seen = new Set();
  return results.filter(log => {
    const key = log.raw;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}