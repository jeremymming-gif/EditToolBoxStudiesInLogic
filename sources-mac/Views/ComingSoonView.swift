import SwiftUI

/// 尚未接入的功能页占位（开发过程中使用）。
struct ComingSoonView: View {

    let help: String
    let note: String

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(help)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            HStack(spacing: 8) {
                Image(systemName: "hammer")
                Text(note).font(.callout)
            }
            .foregroundStyle(.tertiary)
            Spacer()
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}
