import AppKit
import ImageIO
import SwiftUI

/// Downsampled images for thumbnails and previews, decoded once (ImageIO reads only what the size needs).
@MainActor final class ImageStore {
    static let shared = ImageStore()
    private var cache: [String: NSImage] = [:]

    func image(_ url: URL, maxPixels: Int) -> NSImage? {
        let source = Self.displayable(url)
        let key = "\(source.path)#\(maxPixels)#\(Self.modified(source))"
        if let hit = cache[key] { return hit }
        guard let imageSource = CGImageSourceCreateWithURL(source as CFURL, nil),
              let image = CGImageSourceCreateThumbnailAtIndex(imageSource, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceThumbnailMaxPixelSize: maxPixels,
                kCGImageSourceCreateThumbnailWithTransform: true,
              ] as CFDictionary) else { return nil }
        let result = NSImage(cgImage: image, size: NSSize(width: image.width, height: image.height))
        if cache.count > 200 { cache.removeAll() }
        cache[key] = result
        return result
    }

    /// A project shows as its photo layer (the bottom one); anything else as itself.
    static func displayable(_ url: URL) -> URL {
        guard url.pathExtension.lowercased() == "comp",
              let data = try? Data(contentsOf: url.appendingPathComponent("manifest.json")),
              let manifest = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let layers = manifest["layers"] as? [[String: Any]],
              let file = layers.first(where: { $0["imageFile"] != nil && ($0["isGroup"] as? Bool) != true })?["imageFile"] as? String
        else { return url }
        return url.appendingPathComponent("images").appendingPathComponent(file)
    }

    private static func modified(_ url: URL) -> TimeInterval {
        ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
    }
}

/// An image from disk, filling its frame (cropped) or fitting in it; a soft placeholder until it is there.
struct DiskImage: View {
    let url: URL?
    var maxPixels = 400
    var fill = true
    var body: some View {
        if let url, let image = ImageStore.shared.image(url, maxPixels: maxPixels) {
            Image(nsImage: image)
                .resizable()
                .interpolation(.high)
                .aspectRatio(contentMode: fill ? .fill : .fit)
        } else {
            ZStack {
                Rectangle().fill(.primary.opacity(0.06))
                Image(systemName: "photo").font(.system(size: 18)).foregroundStyle(.tertiary)
            }
        }
    }
}
