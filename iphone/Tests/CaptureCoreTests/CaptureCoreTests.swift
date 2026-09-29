import XCTest
@testable import CaptureCore

final class CaptureCoreTests: XCTestCase {
    func testScaleAndRotatedCoordinateFrame() {
        let f=CaptureFrame(origin: .init(10,20,30), right: .init(0,0,-1), up: .init(0,1,0), outward: .init(1,0,0))
        let p=f.millimeters(from: .init(10.03,20.02,29.9))
        XCTAssertEqual(p.x,100,accuracy: 1e-8); XCTAssertEqual(p.y,20,accuracy: 1e-8); XCTAssertEqual(p.z,30,accuracy: 1e-8)
    }
    func testDemoHasMeasuredSizeAndOutwardWinding() throws {
        let m=SurfaceMesh.demo(); try m.validate()
        XCTAssertEqual(m.dimensions.x,300); XCTAssertEqual(m.dimensions.y,300)
        for t in m.triangles { XCTAssertGreaterThan((m.vertices[t.b]-m.vertices[t.a]).cross(m.vertices[t.c]-m.vertices[t.a]).z,0) }
    }
    func testCropCompactsIndicesAndKeepsBoundaryOpen() throws {
        let m=SurfaceMesh.demo().cropped(widthMM: 120,depthMM: 400)
        try m.validate(); XCTAssertEqual(m.dimensions.x,120)
        XCTAssertEqual(Set(m.triangles.flatMap(\.indices)).count,m.vertices.count)
        XCTAssertTrue(m.vertices.allSatisfy { abs($0.x)<=60 && abs($0.y)<=60 })
    }
    func testMergeOffsetsFaces() throws {
        let one=SurfaceMesh(vertices:[.init(0,0,0),.init(1,0,0),.init(0,1,0)],triangles:[.init(0,1,2)])
        let merged=try SurfaceMesh.merge([one,one])
        XCTAssertEqual(merged.triangles[1],.init(3,4,5)); XCTAssertEqual(merged.vertices.count,6)
    }
    func testInvalidMeshCannotExport() {
        let invalid=SurfaceMesh(vertices:[.init(.nan,0,0)],triangles:[.init(0,1,2)])
        XCTAssertThrowsError(try invalid.obj(isDemo: false)); XCTAssertThrowsError(try SurfaceMesh().obj(isDemo: false))
    }
    func testOBJIncludesMillimetersAndDemoLabel() throws {
        let obj=String(decoding: try SurfaceMesh.demo().obj(isDemo: true),as: UTF8.self)
        XCTAssertTrue(obj.contains("units: millimeters")); XCTAssertTrue(obj.contains("SYNTHETIC DEMO")); XCTAssertTrue(obj.contains("f 1 2 53\n"))
    }
    func testExportCancellation() { XCTAssertThrowsError(try SurfaceMesh.demo().obj(isDemo: false,cancelled: { true })) }
    func testCRC32KnownVector() { XCTAssertEqual(CaptureArchive.crc32(Data("123456789".utf8)),0xcbf43926) }
    func testArchiveRejectsTraversalAndDuplicatePaths() {
        let url=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        XCTAssertThrowsError(try CaptureArchive.write([.init("../bad",Data())],to: url))
        XCTAssertThrowsError(try CaptureArchive.write([.init("a",Data()),.init("a",Data())],to: url))
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }
    func testArchiveCancellationPreservesExistingFile() throws {
        let url=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:url) }
        try Data("existing".utf8).write(to:url)
        XCTAssertThrowsError(try CaptureArchive.write([.init("capture.json",Data())],to:url,cancelled:{ true }))
        XCTAssertEqual(try Data(contentsOf:url),Data("existing".utf8))
    }
    func testCropExcludesWholeCrossingTrianglesAndInvalidIndices() {
        let m=SurfaceMesh(vertices:[.init(0,0,0),.init(100,0,0),.init(0,1,0)],triangles:[.init(0,1,2),.init(-1,0,2)])
        let cropped=m.cropped(widthMM:20,depthMM:20)
        XCTAssertTrue(cropped.triangles.isEmpty); XCTAssertTrue(cropped.vertices.isEmpty)
    }
    func testPhotoReportDoesNotClaimGeometry() throws {
        let r=CaptureReport(kind: "photo-reference-only",mesh: nil,frame: nil,width: 0,depth: 0,reference: nil,
                            referenceDescription: "",notes: "",device: "test",systemVersion: "test",photoCount: 3)
        let decoded=try JSONDecoder().decode(CaptureReport.self,from:r.json())
        XCTAssertNil(decoded.geometryFile); XCTAssertEqual(decoded.triangleCount,0); XCTAssertEqual(decoded.photoCount,3)
    }
}
