import Foundation
import CaptureCore

let folder=URL(fileURLWithPath: CommandLine.arguments.dropFirst().first ?? "test-output")
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
let mesh=SurfaceMesh.demo()
let obj=try mesh.obj(isDemo: true)
let report=CaptureReport(kind: "synthetic-demo", mesh: mesh, frame: nil, width: 300, depth: 400,
                         reference: 300, referenceDescription: "Synthetic square width", notes: "Automated interoperability fixture",
                         device: "Swift test runner", systemVersion: "not iOS", photoCount: 0)
try obj.write(to: folder.appendingPathComponent("surface-mm.obj"))
try CaptureArchive.write([.init("surface-mm.obj",obj), .init("capture.json",try report.json()),
                         .init("reference/échelle.txt",Data("UTF-8 filename test".utf8))], to: folder.appendingPathComponent("capture-demo.zip"))
print("Exported \(mesh.vertices.count) vertices and \(mesh.triangles.count) triangles to \(folder.path)")
