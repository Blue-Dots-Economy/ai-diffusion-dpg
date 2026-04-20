import '@testing-library/jest-dom'

// jsdom does not implement scrollIntoView — provide a no-op stub
window.HTMLElement.prototype.scrollIntoView = function () {}

// jsdom does not implement window.alert — provide a no-op stub
window.alert = function () {}
