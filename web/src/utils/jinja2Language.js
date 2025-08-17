/**
 * Jinja2 Language Definition for Monaco Editor
 * 
 * This provides comprehensive syntax highlighting for Jinja2 templates
 * including all delimiters, control structures, filters, and tests.
 */

export const JINJA2_LANGUAGE_ID = 'jinja2';

export const jinja2LanguageDefinition = {
  defaultToken: '',
  tokenPostfix: '.jinja2',
  
  // Jinja2 keywords
  keywords: [
    'if', 'elif', 'else', 'endif',
    'for', 'in', 'endfor',
    'block', 'endblock',
    'extends', 'include', 'import', 'from',
    'set', 'macro', 'endmacro',
    'call', 'endcall',
    'filter', 'endfilter',
    'with', 'endwith',
    'autoescape', 'endautoescape',
    'raw', 'endraw',
    'do', 'continue', 'break'
  ],
  
  // Jinja2 built-in filters
  filters: [
    'abs', 'attr', 'batch', 'capitalize', 'center', 'default', 'd',
    'dictsort', 'escape', 'e', 'filesizeformat', 'first', 'float',
    'forceescape', 'format', 'groupby', 'indent', 'int', 'join',
    'last', 'length', 'list', 'lower', 'map', 'max', 'min',
    'pprint', 'random', 'reject', 'rejectattr', 'replace', 'reverse',
    'round', 'safe', 'select', 'selectattr', 'slice', 'sort', 'string',
    'striptags', 'sum', 'title', 'tojson', 'trim', 'truncate',
    'unique', 'upper', 'urlencode', 'urlize', 'wordcount', 'wordwrap',
    'xmlattr'
  ],
  
  // Jinja2 tests
  tests: [
    'callable', 'defined', 'divisibleby', 'equalto', 'escaped',
    'even', 'false', 'ge', 'gt', 'in', 'integer', 'iterable',
    'le', 'lower', 'lt', 'mapping', 'ne', 'none', 'number',
    'odd', 'sameas', 'sequence', 'string', 'true', 'undefined',
    'upper'
  ],
  
  operators: [
    '=', '>', '<', '!', '~', '?', ':',
    '==', '<=', '>=', '!=', '&&', '||', '++', '--',
    '+', '-', '*', '/', '%', '**', '//',
    '+=', '-=', '*=', '/=', '%=',
    '&', '|', '^', '>>', '<<',
    'and', 'or', 'not', 'is', 'in'
  ],
  
  // Define different bracket types
  brackets: [
    ['{#', '#}', 'delimiter.comment'],
    ['{%', '%}', 'delimiter.statement'],
    ['{{', '}}', 'delimiter.expression'],
    ['[', ']', 'delimiter.square'],
    ['(', ')', 'delimiter.parenthesis'],
    ['<', '>', 'delimiter.angle']
  ],
  
  // Tokenizer
  tokenizer: {
    root: [
      // Jinja2 Comments
      [/{#/, 'comment.jinja', '@jinja_comment'],
      
      // Jinja2 Statements
      [/{%-?\s*/, 'keyword.jinja', '@jinja_statement'],
      
      // Jinja2 Expressions
      [/{{-?\s*/, 'variable.jinja', '@jinja_expression'],
      
      // HTML/XML
      [/<!DOCTYPE/, 'metatag.html', '@doctype'],
      [/<!--/, 'comment.html', '@comment'],
      [/(<)((?:[\w\-]+:)?[\w\-]+)(\s*)(\/>)/, ['delimiter.html', 'tag.html', '', 'delimiter.html']],
      [/(<)(script)/, ['delimiter.html', { token: 'tag.html', next: '@script' }]],
      [/(<)(style)/, ['delimiter.html', { token: 'tag.html', next: '@style' }]],
      [/(<)((?:[\w\-]+:)?[\w\-]+)/, ['delimiter.html', { token: 'tag.html', next: '@otherTag' }]],
      [/(<\/)((?:[\w\-]+:)?[\w\-]+)/, ['delimiter.html', { token: 'tag.html', next: '@otherTag' }]],
      [/</, 'delimiter.html'],
      [/[^<{]+/] // text
    ],
    
    jinja_comment: [
      [/#}/, 'comment.jinja', '@pop'],
      [/./, 'comment.jinja']
    ],
    
    jinja_statement: [
      [/\s*-?%}/, 'keyword.jinja', '@pop'],
      [/\b(if|elif|else|endif|for|in|endfor|block|endblock|extends|include|import|from|set|macro|endmacro|call|endcall|filter|endfilter|with|endwith|autoescape|endautoescape|raw|endraw|do|continue|break)\b/, 'keyword.control.jinja'],
      [/\b(and|or|not|is|in)\b/, 'keyword.operator.jinja'],
      [/\b(true|false|none)\b/, 'constant.language.jinja'],
      [/\b(loop|super|self|varargs|kwargs)\b/, 'variable.language.jinja'],
      [/"([^"\\]|\\.)*"/, 'string.jinja'],
      [/'([^'\\]|\\.)*'/, 'string.jinja'],
      [/\b\d+(\.\d+)?\b/, 'number.jinja'],
      [/\|/, 'operator.jinja', '@filter'],
      [/[a-zA-Z_]\w*/, 'variable.jinja'],
      [/[=<>!]=?/, 'operator.jinja'],
      [/[+\-*\/%]/, 'operator.jinja'],
      [/[\[\]\(\),\.]/, 'punctuation.jinja']
    ],
    
    jinja_expression: [
      [/\s*-?}}/, 'variable.jinja', '@pop'],
      [/\b(and|or|not|is|in)\b/, 'keyword.operator.jinja'],
      [/\b(true|false|none)\b/, 'constant.language.jinja'],
      [/\b(loop|super|self|varargs|kwargs)\b/, 'variable.language.jinja'],
      [/"([^"\\]|\\.)*"/, 'string.jinja'],
      [/'([^'\\]|\\.)*'/, 'string.jinja'],
      [/\b\d+(\.\d+)?\b/, 'number.jinja'],
      [/\|/, 'operator.jinja', '@filter'],
      [/[a-zA-Z_]\w*/, 'variable.jinja'],
      [/[=<>!]=?/, 'operator.jinja'],
      [/[+\-*\/%]/, 'operator.jinja'],
      [/[\[\]\(\),\.]/, 'punctuation.jinja']
    ],
    
    filter: [
      [/\s+/, ''],
      [/[a-zA-Z_]\w*/, {
        cases: {
          '@filters': 'support.function.jinja',
          '@default': 'function.jinja'
        }
      }],
      [/\(/, 'punctuation.jinja', '@filter_args'],
      [/\|/, 'operator.jinja'],
      [/(?=[%}])/, '', '@pop']
    ],
    
    filter_args: [
      [/\)/, 'punctuation.jinja', '@pop'],
      [/"([^"\\]|\\.)*"/, 'string.jinja'],
      [/'([^'\\]|\\.)*'/, 'string.jinja'],
      [/\b\d+(\.\d+)?\b/, 'number.jinja'],
      [/[a-zA-Z_]\w*/, 'variable.jinja'],
      [/,/, 'punctuation.jinja']
    ],
    
    // HTML states
    doctype: [
      [/[^>]+/, 'metatag.content.html'],
      [/>/, 'metatag.html', '@pop']
    ],
    
    comment: [
      [/-->/, 'comment.html', '@pop'],
      [/[^-]+/, 'comment.content.html'],
      [/./, 'comment.content.html']
    ],
    
    otherTag: [
      [/\/?>/, 'delimiter.html', '@pop'],
      [/"([^"]*)"/, 'attribute.value.html'],
      [/'([^']*)'/, 'attribute.value.html'],
      [/[\w\-]+/, 'attribute.name.html'],
      [/=/, 'delimiter.html'],
      [/[ \t\r\n]+/] // whitespace
    ],
    
    script: [
      [/type/, 'attribute.name.html', '@scriptAfterType'],
      [/"([^"]*)"/, 'attribute.value.html'],
      [/'([^']*)'/, 'attribute.value.html'],
      [/[\w\-]+/, 'attribute.name.html'],
      [/=/, 'delimiter.html'],
      [/>/, { token: 'delimiter.html', next: '@scriptEmbedded', nextEmbedded: 'text/javascript' }],
      [/[ \t\r\n]+/],
      [/(<\/)(script\s*)(>)/, ['delimiter.html', 'tag.html', { token: 'delimiter.html', next: '@pop' }]]
    ],
    
    scriptAfterType: [
      [/=/, 'delimiter.html', '@scriptAfterTypeEquals'],
      [/>/, { token: 'delimiter.html', next: '@scriptEmbedded', nextEmbedded: 'text/javascript' }],
      [/[ \t\r\n]+/],
      [/<\/script\s*>/, { token: '@rematch', next: '@pop' }]
    ],
    
    scriptAfterTypeEquals: [
      [/"([^"]*)"/, { token: 'attribute.value.html', switchTo: '@scriptWithCustomType.$1' }],
      [/'([^']*)'/, { token: 'attribute.value.html', switchTo: '@scriptWithCustomType.$1' }],
      [/>/, { token: 'delimiter.html', next: '@scriptEmbedded', nextEmbedded: 'text/javascript' }],
      [/[ \t\r\n]+/],
      [/<\/script\s*>/, { token: '@rematch', next: '@pop' }]
    ],
    
    scriptWithCustomType: [
      [/>/, { token: 'delimiter.html', next: '@scriptEmbedded.$S2', nextEmbedded: '$S2' }],
      [/"([^"]*)"/, 'attribute.value.html'],
      [/'([^']*)'/, 'attribute.value.html'],
      [/[\w\-]+/, 'attribute.name.html'],
      [/=/, 'delimiter.html'],
      [/[ \t\r\n]+/],
      [/<\/script\s*>/, { token: '@rematch', next: '@pop' }]
    ],
    
    scriptEmbedded: [
      [/<\/script/, { token: '@rematch', next: '@pop', nextEmbedded: '@pop' }],
      [/[^<]+/, '']
    ],
    
    style: [
      [/type/, 'attribute.name.html', '@styleAfterType'],
      [/"([^"]*)"/, 'attribute.value.html'],
      [/'([^']*)'/, 'attribute.value.html'],
      [/[\w\-]+/, 'attribute.name.html'],
      [/=/, 'delimiter.html'],
      [/>/, { token: 'delimiter.html', next: '@styleEmbedded', nextEmbedded: 'text/css' }],
      [/[ \t\r\n]+/],
      [/(<\/)(style\s*)(>)/, ['delimiter.html', 'tag.html', { token: 'delimiter.html', next: '@pop' }]]
    ],
    
    styleAfterType: [
      [/=/, 'delimiter.html', '@styleAfterTypeEquals'],
      [/>/, { token: 'delimiter.html', next: '@styleEmbedded', nextEmbedded: 'text/css' }],
      [/[ \t\r\n]+/],
      [/<\/style\s*>/, { token: '@rematch', next: '@pop' }]
    ],
    
    styleAfterTypeEquals: [
      [/"([^"]*)"/, { token: 'attribute.value.html', switchTo: '@styleWithCustomType.$1' }],
      [/'([^']*)'/, { token: 'attribute.value.html', switchTo: '@styleWithCustomType.$1' }],
      [/>/, { token: 'delimiter.html', next: '@styleEmbedded', nextEmbedded: 'text/css' }],
      [/[ \t\r\n]+/],
      [/<\/style\s*>/, { token: '@rematch', next: '@pop' }]
    ],
    
    styleWithCustomType: [
      [/>/, { token: 'delimiter.html', next: '@styleEmbedded.$S2', nextEmbedded: '$S2' }],
      [/"([^"]*)"/, 'attribute.value.html'],
      [/'([^']*)'/, 'attribute.value.html'],
      [/[\w\-]+/, 'attribute.name.html'],
      [/=/, 'delimiter.html'],
      [/[ \t\r\n]+/],
      [/<\/style\s*>/, { token: '@rematch', next: '@pop' }]
    ],
    
    styleEmbedded: [
      [/<\/style/, { token: '@rematch', next: '@pop', nextEmbedded: '@pop' }],
      [/[^<]+/, '']
    ]
  }
};

// Theme colors for Jinja2
export const jinja2ThemeRules = [
  { token: 'comment.jinja', foreground: '608B4E' },
  { token: 'keyword.jinja', foreground: 'C586C0' },
  { token: 'keyword.control.jinja', foreground: 'C586C0' },
  { token: 'keyword.operator.jinja', foreground: '569CD6' },
  { token: 'variable.jinja', foreground: '9CDCFE' },
  { token: 'variable.language.jinja', foreground: '4EC9B0' },
  { token: 'string.jinja', foreground: 'CE9178' },
  { token: 'number.jinja', foreground: 'B5CEA8' },
  { token: 'constant.language.jinja', foreground: '569CD6' },
  { token: 'support.function.jinja', foreground: 'DCDCAA' },
  { token: 'function.jinja', foreground: 'DCDCAA' },
  { token: 'operator.jinja', foreground: 'D4D4D4' },
  { token: 'punctuation.jinja', foreground: 'D4D4D4' },
  { token: 'delimiter.comment', foreground: '608B4E', fontStyle: 'bold' },
  { token: 'delimiter.statement', foreground: 'C586C0', fontStyle: 'bold' },
  { token: 'delimiter.expression', foreground: '9CDCFE', fontStyle: 'bold' }
];

// Configuration for Jinja2
export const jinja2Configuration = {
  comments: {
    blockComment: ['{#', '#}']
  },
  brackets: [
    ['{#', '#}'],
    ['{%', '%}'],
    ['{{', '}}'],
    ['[', ']'],
    ['(', ')']
  ],
  autoClosingPairs: [
    { open: '{#', close: '#}' },
    { open: '{%', close: '%}' },
    { open: '{{', close: '}}' },
    { open: '[', close: ']' },
    { open: '(', close: ')' },
    { open: '"', close: '"' },
    { open: "'", close: "'" }
  ],
  surroundingPairs: [
    { open: '{#', close: '#}' },
    { open: '{%', close: '%}' },
    { open: '{{', close: '}}' },
    { open: '"', close: '"' },
    { open: "'", close: "'" }
  ]
};

// Register the language with Monaco
export function registerJinja2Language(monaco) {
  // Register language
  monaco.languages.register({ id: JINJA2_LANGUAGE_ID });
  
  // Set language configuration
  monaco.languages.setLanguageConfiguration(JINJA2_LANGUAGE_ID, jinja2Configuration);
  
  // Set tokenizer
  monaco.languages.setMonarchTokensProvider(JINJA2_LANGUAGE_ID, jinja2LanguageDefinition);
  
  // Define theme
  monaco.editor.defineTheme('jinja2-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: jinja2ThemeRules,
    colors: {}
  });
}

// Helper to create completions for Jinja2
export function getJinja2Completions(monaco, position) {
  const suggestions = [];
  
  // Add keywords
  jinja2LanguageDefinition.keywords.forEach(keyword => {
    suggestions.push({
      label: keyword,
      kind: monaco.languages.CompletionItemKind.Keyword,
      insertText: keyword,
      detail: 'Jinja2 keyword'
    });
  });
  
  // Add filters with pipe
  jinja2LanguageDefinition.filters.forEach(filter => {
    suggestions.push({
      label: `| ${filter}`,
      kind: monaco.languages.CompletionItemKind.Function,
      insertText: `| ${filter}`,
      detail: 'Jinja2 filter'
    });
  });
  
  // Add tests
  jinja2LanguageDefinition.tests.forEach(test => {
    suggestions.push({
      label: `is ${test}`,
      kind: monaco.languages.CompletionItemKind.Function,
      insertText: `is ${test}`,
      detail: 'Jinja2 test'
    });
  });
  
  // Add common snippets
  const snippets = [
    {
      label: 'if',
      kind: monaco.languages.CompletionItemKind.Snippet,
      insertText: '{% if ${1:condition} %}\n\t$0\n{% endif %}',
      insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
      detail: 'If statement'
    },
    {
      label: 'for',
      kind: monaco.languages.CompletionItemKind.Snippet,
      insertText: '{% for ${1:item} in ${2:items} %}\n\t$0\n{% endfor %}',
      insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
      detail: 'For loop'
    },
    {
      label: 'block',
      kind: monaco.languages.CompletionItemKind.Snippet,
      insertText: '{% block ${1:name} %}\n\t$0\n{% endblock %}',
      insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
      detail: 'Template block'
    },
    {
      label: 'macro',
      kind: monaco.languages.CompletionItemKind.Snippet,
      insertText: '{% macro ${1:name}(${2:args}) %}\n\t$0\n{% endmacro %}',
      insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
      detail: 'Macro definition'
    }
  ];
  
  suggestions.push(...snippets);
  
  return { suggestions };
}