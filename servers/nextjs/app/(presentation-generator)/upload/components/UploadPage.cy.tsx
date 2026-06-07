// Component test for the (fork-rewritten) UploadPage. The upstream spec tested
// behavior this fork removed (English "Next" button, /theme + /documents-preview
// navigation, research-mode toggle, titles/report endpoints). This rewrite covers
// what the fork actually ships: the Korean upload surface mounts with all its
// controls, prompt/slides/language inputs work, file attachment lists + toasts,
// and empty-input validation. Toasts only render when a <Toaster/> is in the tree
// (notify -> sonner), so the mount wrapper includes one — mirroring the real app.
import React from 'react'
import UploadPage from './UploadPage'
import { mount } from 'cypress/react'
import { store } from '@/store/store'
import { Provider } from 'react-redux'
import { AppRouterContext } from 'next/dist/shared/lib/app-router-context.shared-runtime'
import { Toaster } from '@/components/ui/sonner'

import '@/app/globals.css'

const createRouter = () => ({
  push: cy.stub().as('router.push'),
  back: cy.stub(),
  forward: cy.stub(),
  refresh: cy.stub(),
  replace: cy.stub(),
  prefetch: cy.stub().resolves(),
  route: '/',
  pathname: '/',
  query: {},
  asPath: '/',
})

const mountUploadPage = () =>
  mount(
    <AppRouterContext.Provider value={createRouter() as any}>
      <Provider store={store}>
        <UploadPage />
        <Toaster />
      </Provider>
    </AppRouterContext.Provider>
  )

// notify() -> sonner renders <li data-sonner-toast> with the title in [data-title].
const checkToast = (message: string) =>
  cy.get('[data-sonner-toast]', { timeout: 6000 }).should('contain', message)

describe('<UploadPage />', () => {
  beforeEach(() => {
    cy.viewport(1440, 900)
    mountUploadPage()
  })

  it('renders the upload surface with all controls', () => {
    cy.contains('무엇을 만들어 드릴까요?').should('exist')
    cy.contains('button', '생성하기').should('exist')
    cy.get('[data-testid="prompt-input"]').should('exist')
    cy.get('[data-testid="slides-select"]').should('exist')
    // Language + tone/verbosity live behind the advanced-settings modal.
    cy.get('[data-testid="advanced-settings-button"]').should('exist')
    cy.get('[data-testid="file-upload-input"]').should('exist')
  })

  describe('Configuration Selection', () => {
    it('allows selecting number of slides', () => {
      cy.get('[data-testid="slides-select"]').click({ force: true })
      // The slide popover is fixed-positioned and overlaps the template grid in
      // the component-test layout, so force the click past the cover check.
      cy.get('[role="option"]').contains('슬라이드 12개').click({ force: true })
      cy.get('[data-testid="slides-select"]').should('contain', '12')
    })

    it('opens the advanced-settings modal', () => {
      cy.get('[data-testid="advanced-settings-button"]').click()
      cy.get('[role="dialog"]').should('be.visible').and('contain', '고급 설정')
    })
  })

  describe('Prompt Input', () => {
    it('allows entering prompt text', () => {
      const testPrompt = 'Create a presentation about AI'
      cy.get('[data-testid="prompt-input"]').type(testPrompt)
      cy.get('[data-testid="prompt-input"]').should('have.value', testPrompt)
    })
  })

  describe('File Upload', () => {
    it('handles a single document upload', () => {
      cy.fixture('example.txt').as('testFile')
      cy.get('[data-testid="file-upload-input"]').selectFile('@testFile', { force: true })
      cy.get('[data-testid="file-list"]').should('contain', 'example.txt')
      checkToast('파일 선택 완료')
    })

    it('handles multiple document uploads', () => {
      const file1 = new File(['content1'], 'document1.txt', { type: 'text/plain' })
      const file2 = new File(['content2'], 'document2.txt', { type: 'text/plain' })
      cy.get('[data-testid="file-upload-input"]').selectFile(
        [
          { contents: file1, fileName: 'document1.txt' },
          { contents: file2, fileName: 'document2.txt' },
        ],
        { force: true }
      )
      cy.get('[data-testid="file-list"]').within(() => {
        cy.contains('document1.txt').should('be.visible')
        cy.contains('document2.txt').should('be.visible')
      })
      checkToast('파일 선택 완료')
    })

    it('handles an image upload', () => {
      const imageFile = new File(['image content'], 'test-image.jpg', { type: 'image/jpeg' })
      cy.get('[data-testid="file-upload-input"]').selectFile(
        { contents: imageFile, fileName: 'test-image.jpg', mimeType: 'image/jpeg' },
        { force: true }
      )
      cy.get('[data-testid="file-list"]').should('contain', 'test-image.jpg')
      checkToast('파일 선택 완료')
    })

    it('handles mixed document and image uploads', () => {
      const docFile = new File(['doc content'], 'test-doc.txt', { type: 'text/plain' })
      const imageFile = new File(['image content'], 'test-image.jpg', { type: 'image/jpeg' })
      cy.get('[data-testid="file-upload-input"]').selectFile(
        [
          { contents: docFile, fileName: 'test-doc.txt' },
          { contents: imageFile, fileName: 'test-image.jpg', mimeType: 'image/jpeg' },
        ],
        { force: true }
      )
      cy.get('[data-testid="file-list"]').within(() => {
        cy.contains('test-doc.txt').should('be.visible')
        cy.contains('test-image.jpg').should('be.visible')
      })
      checkToast('파일 선택 완료')
    })
  })

  describe('Validation', () => {
    it('warns when no prompt or document is provided', () => {
      cy.contains('button', '생성하기').click()
      checkToast('입력이 필요합니다')
    })
  })

  describe('Theme gallery', () => {
    it('lists presets and selecting one marks it active', () => {
      cy.get('[data-testid="theme-gallery"]').should('exist')
      cy.get('[data-testid="theme-option-carbon"]').should('exist')
      cy.get('[data-testid="theme-option-broadsheet"]').should('exist')
      cy.get('[data-testid="theme-option-carbon"]').click()
      cy.get('[data-testid="theme-option-carbon"]').should('have.attr', 'aria-checked', 'true')
      cy.get('[data-testid="theme-option-none"]').should('have.attr', 'aria-checked', 'false')
    })
  })
})
