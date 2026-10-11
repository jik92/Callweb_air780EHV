import { readdir, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import ts from 'typescript'

const root = fileURLToPath(new URL('../src/', import.meta.url))
const failures = []
const forbiddenStyles = /\.(css|scss|sass|less|styl|stylus)$/i
const cssLibraries = /^(styled-components|@emotion\/(react|styled|css)|styled-jsx)(\/|$)/
const entry = path.join(root, 'tailwind.css')
const expectedEntry = /^@import (["'])tailwindcss\1;$/

async function visit(directory) {
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const filename = path.join(directory, item.name)
    if (item.isDirectory()) {
      await visit(filename)
      continue
    }
    const source = await readFile(filename, 'utf8')
    const relative = path.relative(root, filename)
    if (forbiddenStyles.test(filename)) {
      if (filename !== entry || !expectedEntry.test(source.trim())) {
        failures.push(`${relative}: 仅允许 tailwind.css 中的 Tailwind 导入指令，禁止手写样式。`)
      }
      continue
    }
    if (!/\.[cm]?[jt]sx?$/.test(filename)) continue
    const ast = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true)
    function report(node, message) {
      const { line } = ast.getLineAndCharacterOfPosition(node.getStart(ast))
      failures.push(`${relative}:${line + 1}: ${message}`)
    }
    function walk(node) {
      if (ts.isJsxAttribute(node) && ['style', 'styles'].includes(node.name.getText(ast))) {
        report(node, '禁止 style/styles 属性；使用完整的 Tailwind className。')
      }
      if (
        (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
        node.tagName.getText(ast) === 'style'
      ) {
        report(node, '禁止 <style> 标签。')
      }
      if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
        const name = node.moduleSpecifier.text
        if (cssLibraries.test(name) || (forbiddenStyles.test(name) && name !== './tailwind.css')) {
          report(node, '禁止导入手写 CSS、CSS Modules 或 CSS-in-JS 样式。')
        }
      }
      if (
        ts.isPropertyAssignment(node) &&
        ['style', 'styles'].includes(node.name.getText(ast).replace(/["']/g, ''))
      ) {
        report(
          node,
          '禁止通过对象传入 style/styles；使用 className/classNames 或 Ant Design token。',
        )
      }
      ts.forEachChild(node, walk)
    }
    walk(ast)
  }
}
await visit(root)
if (failures.length) {
  console.error(failures.join('\n'))
  process.exitCode = 1
} else {
  console.log('Tailwind 样式约束检查通过。')
}
