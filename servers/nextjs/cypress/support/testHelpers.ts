/**
 * Cypress Component Test Helpers
 *
 * Provides shared utilities for component testing:
 * - Mock router creation
 * - API intercept setup and cleanup
 * - Test state management
 *
 * Usage:
 *   import { createMockRouter, setupApiMocks, cleanupApiMocks } from '@/cypress/support/testHelpers'
 */

import type { AppRouterInstance } from 'next/dist/shared/lib/app-router-context.shared-runtime';

/**
 * Creates a mock Next.js router for component testing
 *
 * @returns Mocked AppRouter instance with stubbed methods
 */
export function createMockRouter(): Partial<AppRouterInstance> {
  return {
    push: cy.stub().resolves(),
    back: cy.stub(),
    forward: cy.stub(),
    refresh: cy.stub(),
    replace: cy.stub(),
    prefetch: cy.stub().resolves(),
  };
}

/**
 * API Mock Setup and Cleanup
 */
export class ApiMockManager {
  private aliases: string[] = [];

  /**
   * Set up HTTP intercept for a GET endpoint
   *
   * @param pattern - URL pattern (supports wildcards, e.g., "URL_PATTERN_EXAMPLE")
   * @param response - Response body or handler function
   * @param alias - Cypress alias for cy.wait()
   */
  setupGetIntercept(
    pattern: string,
    response: Record<string, any> | ((req: any) => Record<string, any>),
    alias: string = 'getRequest'
  ): void {
    const handler = typeof response === 'function'
      ? (request: any) => {
          request.reply({
            statusCode: 200,
            body: response(request),
          });
        }
      : (request: any) => {
          request.reply({
            statusCode: 200,
            body: response,
          });
        };

    cy.intercept('GET', pattern, handler).as(alias);
    this.aliases.push(alias);
  }

  /**
   * Set up HTTP intercept for a POST endpoint
   */
  setupPostIntercept(
    pattern: string,
    response: Record<string, any> | ((req: any) => Record<string, any>),
    alias: string = 'postRequest'
  ): void {
    const handler = typeof response === 'function'
      ? (request: any) => {
          request.reply({
            statusCode: 200,
            body: response(request),
          });
        }
      : (request: any) => {
          request.reply({
            statusCode: 200,
            body: response,
          });
        };

    cy.intercept('POST', pattern, handler).as(alias);
    this.aliases.push(alias);
  }

  /**
   * Set up HTTP intercept for a PATCH endpoint
   */
  setupPatchIntercept(
    pattern: string,
    response: Record<string, any> | ((req: any) => Record<string, any>),
    alias: string = 'patchRequest'
  ): void {
    const handler = typeof response === 'function'
      ? (request: any) => {
          request.reply({
            statusCode: 200,
            body: response(request),
          });
        }
      : (request: any) => {
          request.reply({
            statusCode: 200,
            body: response,
          });
        };

    cy.intercept('PATCH', pattern, handler).as(alias);
    this.aliases.push(alias);
  }

  /**
   * Set up HTTP intercept for a DELETE endpoint
   */
  setupDeleteIntercept(
    pattern: string,
    response: Record<string, any>,
    alias: string = 'deleteRequest'
  ): void {
    cy.intercept('DELETE', pattern, {
      statusCode: 200,
      body: response,
    }).as(alias);
    this.aliases.push(alias);
  }

  /**
   * Set up HTTP intercept for an error response
   * @param pattern - URL pattern to match
   * @param statusCode - HTTP status code (default 500)
   * @param message - Error message body
   * @param alias - Cypress alias for cy.wait()
   */
  setupErrorIntercept(
    pattern: string,
    statusCode: number = 500,
    message: string = 'Internal Server Error',
    alias: string = 'errorRequest'
  ): void {
    // Intercept any HTTP method to the given pattern with error response
    cy.intercept('GET', pattern, {
      statusCode,
      body: { error: message },
    }).as(alias);
    cy.intercept('POST', pattern, {
      statusCode,
      body: { error: message },
    });
    cy.intercept('PATCH', pattern, {
      statusCode,
      body: { error: message },
    });
    cy.intercept('DELETE', pattern, {
      statusCode,
      body: { error: message },
    });
    this.aliases.push(alias);
  }

  /**
   * Clean up all registered intercepts and aliases
   */
  cleanup(): void {
    // Note: cy.intercept() doesn't have a direct cleanup method.
    // Aliases are automatically cleaned up between tests.
    // This method is kept for API consistency and future extensibility.
    this.aliases = [];
  }

  /**
   * Get all registered aliases for waiting
   */
  getAliases(): string[] {
    return [...this.aliases];
  }
}

/**
 * Chart rendering helper - waits for async chart rendering to complete
 *
 * Usage:
 *   cy.waitForChartsToRender()
 */
export function waitForChartsToRender(): Cypress.Chainable<void> {
  return cy.get('body').then(($body) => {
    if ($body.find('[data-presenton-charts]').length > 0) {
      // Commands enqueued here are awaited before the outer .then() resolves.
      cy.get('[data-presenton-charts]', { timeout: 10000 })
        .should('have.attr', 'data-presenton-charts', 'ready');
    }
    // Yield undefined so the command's subject matches Chainable<void>.
    return cy.wrap<void>(undefined, { log: false });
  });
}

/**
 * Viewport helper for common test sizes
 */
export const testViewports = {
  desktop: { width: 1440, height: 900 },
  tablet: { width: 768, height: 1024 },
  mobile: { width: 375, height: 667 },
};
