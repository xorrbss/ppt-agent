import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

import JSZip from 'jszip'

const source = resolve(process.argv[2] ?? 'artifacts/synthetic-90.pptx')
const output = resolve(process.argv[3] ?? 'artifacts/policy-features.pptm')
const withoutMacro = process.argv.includes('--without-macro')
const zip = await JSZip.loadAsync(await readFile(source))

const addOverrides = [
  '<Override PartName="/ppt/diagrams/data1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.diagramData+xml"/>',
  '<Override PartName="/ppt/embeddings/oleObject1.bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>',
  '<Override PartName="/ppt/comments/comment1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.comments+xml"/>',
  '<Override PartName="/ppt/commentAuthors.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.commentAuthors+xml"/>',
  ...(withoutMacro
    ? []
    : ['<Override PartName="/ppt/vbaProject.bin" ContentType="application/vnd.ms-office.vbaProject"/>']),
  '<Override PartName="/customXml/item1.xml" ContentType="application/xml"/>',
  '<Override PartName="/ppt/unknown/opaque1.bin" ContentType="application/octet-stream"/>',
]
let contentTypes = await zip.file('[Content_Types].xml').async('string')
contentTypes = contentTypes.replace('</Types>', `${addOverrides.join('')}</Types>`)
zip.file('[Content_Types].xml', contentTypes)

let presentationRels = await zip.file('ppt/_rels/presentation.xml.rels').async('string')
presentationRels = presentationRels.replace(
  '</Relationships>',
  (withoutMacro
    ? ''
    : '<Relationship Id="rIdPolicyMacro" Type="http://schemas.microsoft.com/office/2006/relationships/vbaProject" Target="vbaProject.bin"/>') +
    '<Relationship Id="rIdPolicyCustomXml" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml" Target="../customXml/item1.xml"/>' +
    '<Relationship Id="rIdPolicyOpaque" Type="http://example.invalid/relationships/opaque" Target="unknown/opaque1.bin"/>' +
    '</Relationships>',
)
zip.file('ppt/_rels/presentation.xml.rels', presentationRels)

let slideRels = await zip.file('ppt/slides/_rels/slide1.xml.rels').async('string')
slideRels = slideRels.replace(
  '</Relationships>',
  '<Relationship Id="rIdPolicySmartArt" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData" Target="../diagrams/data1.xml"/>' +
    '<Relationship Id="rIdPolicyOle" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="../embeddings/oleObject1.bin"/>' +
    '<Relationship Id="rIdPolicyComments" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="../comments/comment1.xml"/>' +
    '<Relationship Id="rIdPolicyExternal" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/" TargetMode="External"/>' +
    '</Relationships>',
)
zip.file('ppt/slides/_rels/slide1.xml.rels', slideRels)

let slide = await zip.file('ppt/slides/slide1.xml').async('string')
slide = slide.replace('</p:sld>', '<p:timing/></p:sld>')
zip.file('ppt/slides/slide1.xml', slide)

zip.file('ppt/diagrams/data1.xml', '<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram"/>')
zip.file('ppt/embeddings/oleObject1.bin', Buffer.from('synthetic OLE evidence'))
zip.file('ppt/comments/comment1.xml', '<p:cmLst xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
zip.file('ppt/commentAuthors.xml', '<p:cmAuthorLst xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
if (!withoutMacro) zip.file('ppt/vbaProject.bin', Buffer.from('synthetic macro evidence'))
zip.file('customXml/item1.xml', '<synthetic>custom XML evidence</synthetic>')
zip.file('ppt/unknown/opaque1.bin', Buffer.from('synthetic unknown-part evidence'))

await writeFile(output, await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' }))
console.log(output)
