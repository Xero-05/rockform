import Foundation

public struct ArchiveEntry: Sendable {
    public var name: String; public var data: Data
    public init(_ name: String, _ data: Data) { self.name=name; self.data=data }
}

/// A standard, uncompressed ZIP (UTF-8 names, CRC32, central directory).
/// Uses no third-party runtime or Apple-only compression API.
public enum CaptureArchive {
    private static let crcTable: [UInt32] = (0..<256).map { i in
        var c=UInt32(i)
        for _ in 0..<8 { c = c&1 == 0 ? c>>1 : 0xedb88320 ^ (c>>1) }
        return c
    }
    public static func crc32(_ data: Data) -> UInt32 {
        var value=UInt32.max
        for byte in data { value=crcTable[Int((value ^ UInt32(byte)) & 255)] ^ (value>>8) }
        return value ^ UInt32.max
    }
    public static func write(_ entries: [ArchiveEntry], to url: URL, cancelled: () -> Bool = { false }) throws {
        guard entries.count < 65535 else { throw CaptureError.tooLarge }
        var names=Set<String>()
        var output=Data(), directory=Data()
        for entry in entries {
            if cancelled() { throw CaptureError.cancelled }
            guard !entry.name.isEmpty, !entry.name.hasPrefix("/"), !entry.name.contains("\\"), !entry.name.contains(":"),
                  !entry.name.split(separator: "/").contains(".."), names.insert(entry.name).inserted else { throw CaptureError.invalidArchivePath }
            let name=Data(entry.name.utf8)
            guard name.count<65535, entry.data.count<200_000_000, output.count+entry.data.count<300_000_000 else { throw CaptureError.tooLarge }
            let size=UInt32(entry.data.count), crc=crc32(entry.data), offset=UInt32(output.count)
            output.u32(0x04034b50); output.u16(20); output.u16(0x800); output.u16(0)
            output.u16(0); output.u16(33); output.u32(crc); output.u32(size); output.u32(size)
            output.u16(UInt16(name.count)); output.u16(0); output.append(name); output.append(entry.data)
            directory.u32(0x02014b50); directory.u16(20); directory.u16(20); directory.u16(0x800); directory.u16(0)
            directory.u16(0); directory.u16(33); directory.u32(crc); directory.u32(size); directory.u32(size)
            directory.u16(UInt16(name.count)); directory.u16(0); directory.u16(0); directory.u16(0); directory.u16(0)
            directory.u32(0); directory.u32(offset); directory.append(name)
        }
        let start=UInt32(output.count)
        output.append(directory)
        output.u32(0x06054b50); output.u16(0); output.u16(0)
        output.u16(UInt16(entries.count)); output.u16(UInt16(entries.count))
        output.u32(UInt32(directory.count)); output.u32(start); output.u16(0)
        if cancelled() { throw CaptureError.cancelled }
        try output.write(to: url, options: .atomic)
    }
}

private extension Data {
    mutating func u16(_ value: UInt16) { append(UInt8(value & 255)); append(UInt8(value >> 8)) }
    mutating func u32(_ value: UInt32) { u16(UInt16(value & 65535)); u16(UInt16(value >> 16)) }
}

public struct CaptureReport: Codable, Sendable {
    public var schemaVersion=1
    public var appVersion="0.1.0"
    public var kind: String
    public var createdUTC: String
    public var units="millimeters"
    public var geometryFile: String?
    public var vertexCount: Int
    public var triangleCount: Int
    public var boundsMM: Point3
    public var captureFrame: CaptureFrame?
    public var cropWidthMM: Double
    public var cropDepthMM: Double
    public var measuredReferenceMM: Double?
    public var referenceDescription: String
    public var notes: String
    public var device: String
    public var systemVersion: String
    public var photoCount: Int
    public var limitations: [String]
    public init(kind: String, mesh: SurfaceMesh?, frame: CaptureFrame?, width: Double, depth: Double,
                reference: Double?, referenceDescription: String, notes: String, device: String, systemVersion: String, photoCount: Int) {
        self.kind=kind; createdUTC=ISO8601DateFormatter().string(from: Date())
        geometryFile=mesh == nil ? nil : "surface-mm.obj"
        vertexCount=mesh?.vertices.count ?? 0; triangleCount=mesh?.triangles.count ?? 0; boundsMM=mesh?.dimensions ?? .init(0,0,0)
        captureFrame=frame; cropWidthMM=width; cropDepthMM=depth; measuredReferenceMM=reference
        self.referenceDescription=referenceDescription; self.notes=notes; self.device=device; self.systemVersion=systemVersion; self.photoCount=photoCount
        limitations=["Open surface; not a printable solid.", "Confirm scale using a physical measurement.", "No dimensional or load-bearing qualification."]
        if kind == "synthetic-demo" { limitations.append("Synthetic test surface, not a measurement of real rock.") }
        if kind == "photo-reference-only" { limitations.append("Reference photographs only. No 3D reconstruction or OBJ is produced.") }
        if kind == "lidar" { limitations.append("ARKit scene reconstruction. No raw depth recording, photogrammetry, or custom depth fusion in this lightweight version.") }
    }
    public func json() throws -> Data {
        let encoder=JSONEncoder(); encoder.outputFormatting=[.prettyPrinted, .sortedKeys]
        return try encoder.encode(self)
    }
}
