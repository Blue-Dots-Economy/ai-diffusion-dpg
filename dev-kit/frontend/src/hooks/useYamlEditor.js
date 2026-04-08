import { useRef, useEffect, useCallback } from 'react'
import { EditorView, keymap } from '@codemirror/view'
import { EditorState, Compartment } from '@codemirror/state'
import { basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'
import { defaultKeymap } from '@codemirror/commands'

export default function useYamlEditor(containerRef, content, options = {}) {
  const { readOnly = true, dark = true } = options
  const viewRef = useRef(null)
  const editableComp = useRef(new Compartment())
  const originalRef = useRef('')

  useEffect(() => {
    if (!containerRef.current) return
    if (viewRef.current) {
      viewRef.current.destroy()
      viewRef.current = null
    }

    const extensions = [
      basicSetup,
      yaml(),
      keymap.of(defaultKeymap),
      editableComp.current.of(EditorView.editable.of(!readOnly)),
    ]
    if (dark) extensions.push(oneDark)

    const state = EditorState.create({
      doc: content || '',
      extensions,
    })

    viewRef.current = new EditorView({
      state,
      parent: containerRef.current,
    })

    return () => {
      if (viewRef.current) {
        viewRef.current.destroy()
        viewRef.current = null
      }
    }
  }, [content, dark])

  const startEdit = useCallback(() => {
    if (!viewRef.current) return
    originalRef.current = viewRef.current.state.doc.toString()
    viewRef.current.dispatch({
      effects: editableComp.current.reconfigure(EditorView.editable.of(true)),
    })
  }, [])

  const cancelEdit = useCallback(() => {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      changes: {
        from: 0,
        to: viewRef.current.state.doc.length,
        insert: originalRef.current,
      },
      effects: editableComp.current.reconfigure(EditorView.editable.of(false)),
    })
  }, [])

  const getContent = useCallback(() => {
    if (!viewRef.current) return ''
    return viewRef.current.state.doc.toString()
  }, [])

  const setReadOnly = useCallback((ro) => {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      effects: editableComp.current.reconfigure(EditorView.editable.of(!ro)),
    })
  }, [])

  return { viewRef, startEdit, cancelEdit, getContent, setReadOnly }
}
