import { useState, useRef } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { apiService } from '../services/api'
import Editor from '@monaco-editor/react'
import { Save, RotateCcw, Plus, Info, FileText, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { registerJinja2Language, getJinja2Completions } from '../utils/jinja2Language'

const Templates = () => {
  const [selectedTemplate, setSelectedTemplate] = useState(null)
  const [editorContent, setEditorContent] = useState('')
  const [showCheatsheet, setShowCheatsheet] = useState(false)
  const [isModified, setIsModified] = useState(false)
  const editorRef = useRef(null)
  const monacoRef = useRef(null)
  
  const { data: templates, refetch } = useQuery({
    queryKey: ['templates'],
    queryFn: apiService.getTemplates
  })

  const saveMutation = useMutation({
    mutationFn: ({ name, content }) => apiService.updateTemplate(name, content),
    onSuccess: () => {
      toast.success('Template saved successfully')
      setIsModified(false)
      void refetch()
    }
  })

  const restoreMutation = useMutation({
    mutationFn: (name) => apiService.restoreTemplate(name),
    onSuccess: () => {
      toast.success('Template restored to default')
      void loadTemplate(selectedTemplate)
      void refetch()
    }
  })

  const loadTemplate = async (name) => {
    const response = await apiService.getTemplate(name)
    setSelectedTemplate(name)
    setEditorContent(response.data.content)
    setIsModified(false)
  }

  const handleEditorDidMount = (editor, monaco) => {
    editorRef.current = editor
    monacoRef.current = monaco
    
    // Register Jinja2 language
    registerJinja2Language(monaco)
    
    // Set up auto-completion
    monaco.languages.registerCompletionItemProvider('jinja2', {
      provideCompletionItems: (model, position) => {
        return getJinja2Completions(monaco, position)
      }
    })
    
    // Add keyboard shortcuts
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      if (isModified && selectedTemplate) {
        saveMutation.mutate({ name: selectedTemplate, content: editorContent })
      }
    })
  }

  const handleEditorChange = (value) => {
    setEditorContent(value || '')
    setIsModified(true)
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between p-4 border-b border-dark-border">
        <h2 className="text-2xl font-bold">Template Editor</h2>
        <div className="flex gap-2">
          <button 
            onClick={() => setShowCheatsheet(!showCheatsheet)}
            className="btn btn-secondary"
          >
            <Info size={16} className="mr-2" />
            Jinja2 Guide
          </button>
        </div>
      </div>
      
      <div className="flex-1 flex">
        {/* Template List */}
        <div className="w-64 bg-dark-surface border-r border-dark-border">
          <div className="p-4">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-dark-text-secondary">Templates</h3>
              <button className="p-1 hover:bg-dark-elevated rounded">
                <Plus size={16} />
              </button>
            </div>
            
            <div className="space-y-1">
              {templates?.data?.map(template => (
                <div
                  key={template.name}
                  onClick={() => loadTemplate(template.name)}
                  className={`
                    p-3 rounded-lg cursor-pointer transition-all duration-200
                    hover:bg-dark-elevated
                    ${selectedTemplate === template.name 
                      ? 'bg-gradient-to-r from-jellyfin-purple/20 to-jellyfin-blue/20 border-l-4 border-jellyfin-purple' 
                      : ''
                    }
                  `}
                >
                  <div className="flex items-center gap-2">
                    <FileText size={14} className="text-dark-text-muted" />
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-sm truncate">{template.name}</p>
                      <p className="text-xs text-dark-text-muted">
                        {template['is_default'] ? 'Default' : 'Custom'}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
        
        {/* Editor Area */}
        <div className="flex-1 flex flex-col">
          {selectedTemplate ? (
            <>
              {/* Editor Header */}
              <div className="flex items-center justify-between p-4 bg-dark-elevated border-b border-dark-border">
                <div className="flex items-center gap-4">
                  <h3 className="text-lg font-semibold">{selectedTemplate}.j2</h3>
                  {isModified && (
                    <span className="text-xs text-yellow-500 flex items-center gap-1">
                      <span className="w-2 h-2 bg-yellow-500 rounded-full animate-pulse"></span>
                      Modified
                    </span>
                  )}
                </div>
                <div className="flex gap-2">
                  <button 
                    onClick={() => { 
                      if (selectedTemplate) {
                        void restoreMutation.mutate(selectedTemplate);
                      }
                    }}
                    className="btn btn-secondary"
                    disabled={!templates?.data?.find(t => t.name === selectedTemplate)?.['is_default']}
                  >
                    <RotateCcw size={16} className="mr-2" />
                    Restore Default
                  </button>
                  <button 
                    onClick={() => { saveMutation.mutate({ name: selectedTemplate, content: editorContent }) }}
                    className="btn btn-primary"
                    disabled={!isModified}
                  >
                    <Save size={16} className="mr-2" />
                    Save
                  </button>
                </div>
              </div>
              
              {/* Monaco Editor */}
              <div className="flex-1 relative">
                <Editor
                  height="100%"
                  language="jinja2"
                  theme="jinja2-dark"
                  value={editorContent}
                  onChange={handleEditorChange}
                  onMount={handleEditorDidMount}
                  options={{
                    minimap: { enabled: false },
                    fontSize: 14,
                    fontFamily: "'JetBrains Mono', 'Cascadia Code', Consolas, monospace",
                    wordWrap: 'on',
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    automaticLayout: true,
                    tabSize: 2,
                    insertSpaces: true,
                    folding: true,
                    bracketPairColorization: {
                      enabled: true
                    },
                    suggest: {
                      showKeywords: true,
                      showSnippets: true
                    }
                  }}
                />
                
                {/* Jinja2 Cheatsheet Overlay */}
                {showCheatsheet && (
                  <div className="absolute top-4 right-4 w-96 max-h-[80vh] card overflow-auto z-50">
                    <div className="flex items-center justify-between mb-4">
                      <h4 className="font-semibold text-lg">Jinja2 Quick Reference</h4>
                      <button 
                        onClick={() => setShowCheatsheet(false)}
                        className="p-1 hover:bg-dark-elevated rounded"
                      >
                        <X size={16} />
                      </button>
                    </div>
                    
                    <div className="space-y-4">
                      {/* Variables */}
                      <div>
                        <h5 className="text-sm font-semibold text-jellyfin-purple mb-2">Variables</h5>
                        <div className="space-y-1">
                          <CodeExample 
                            code="{{ item.name }}"
                            description="Output variable"
                          />
                          <CodeExample 
                            code="{{ item.name | default('Unknown') }}"
                            description="With default value"
                          />
                          <CodeExample 
                            code="{{ item.name | truncate(50) }}"
                            description="Apply filter"
                          />
                        </div>
                      </div>
                      
                      {/* Control Structures */}
                      <div>
                        <h5 className="text-sm font-semibold text-jellyfin-purple mb-2">Control Structures</h5>
                        <div className="space-y-1">
                          <CodeExample 
                            code="{% if item.video_height >= 1080 %}\n  HD Content\n{% endif %}"
                            description="Conditional"
                          />
                          <CodeExample 
                            code="{% for genre in item.genres %}\n  {{ genre }}\n{% endfor %}"
                            description="Loop"
                          />
                          <CodeExample 
                            code="{% set quality = 'HD' if item.video_height >= 1080 else 'SD' %}"
                            description="Variable assignment"
                          />
                        </div>
                      </div>
                      
                      {/* Filters */}
                      <div>
                        <h5 className="text-sm font-semibold text-jellyfin-purple mb-2">Common Filters</h5>
                        <div className="space-y-1">
                          <CodeExample 
                            code="{{ item.name | upper }}"
                            description="Uppercase"
                          />
                          <CodeExample 
                            code="{{ item.overview | truncate(200, True, '...') }}"
                            description="Truncate with ellipsis"
                          />
                          <CodeExample 
                            code="{{ item.genres | join(', ') }}"
                            description="Join list"
                          />
                          <CodeExample 
                            code="{{ item.name | tojson }}"
                            description="JSON escape (important!)"
                          />
                        </div>
                      </div>
                      
                      {/* Available Variables */}
                      <div>
                        <h5 className="text-sm font-semibold text-jellyfin-purple mb-2">Available Variables</h5>
                        <div className="text-xs space-y-1 text-dark-text-secondary">
                          <div><span className="text-blue-400">item.name</span> - Media title</div>
                          <div><span className="text-blue-400">item.overview</span> - Description</div>
                          <div><span className="text-blue-400">item.year</span> - Release year</div>
                          <div><span className="text-blue-400">item.genres</span> - List of genres</div>
                          <div><span className="text-blue-400">item.video_height</span> - Resolution (e.g., 1080)</div>
                          <div><span className="text-blue-400">item.video_codec</span> - Codec (e.g., H264)</div>
                          <div><span className="text-blue-400">item.audio_codec</span> - Audio codec</div>
                          <div><span className="text-blue-400">item.media_type</span> - Movie/Series</div>
                          <div><span className="text-blue-400">item.season_number</span> - For TV episodes</div>
                          <div><span className="text-blue-400">item.episode_number</span> - For TV episodes</div>
                        </div>
                      </div>
                      
                      {/* Best Practices */}
                      <div>
                        <h5 className="text-sm font-semibold text-jellyfin-purple mb-2">Best Practices</h5>
                        <ul className="text-xs space-y-1 text-dark-text-secondary">
                          <li>• Always use <code className="text-green-400">| tojson</code> for JSON values</li>
                          <li>• Test with special characters in titles</li>
                          <li>• Use <code className="text-green-400">default()</code> for optional fields</li>
                          <li>• Keep messages under Discord limits</li>
                          <li>• Use <code className="text-green-400">truncate()</code> for long text</li>
                        </ul>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <FileText size={48} className="text-dark-text-muted mx-auto mb-4" />
                <p className="text-dark-text-secondary">Select a template to edit</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// Helper component for code examples
const CodeExample = ({ code, description }) => (
  <div className="bg-dark-elevated p-2 rounded">
    <pre className="text-xs font-mono text-green-400 mb-1 whitespace-pre-wrap">{code}</pre>
    <p className="text-xs text-dark-text-muted">{description}</p>
  </div>
)

export default Templates