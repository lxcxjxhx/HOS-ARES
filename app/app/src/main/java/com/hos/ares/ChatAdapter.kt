package com.hos.ares

import android.net.Uri
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.hos.ares.databinding.ItemChatMessageBinding

/** 聊天消息类型。 */
enum class ChatType { USER, AI, STATUS, HTML }

/** 附件（图片/文件），来自 SAF 选择并已复制到工作目录。 */
data class ChatAttachment(
    val name: String,
    val uri: Uri?,
    val isImage: Boolean,
    val path: String, // 复制后的绝对路径（供 reasonix 读取）
)

/** 单条聊天消息。 */
data class ChatMessage(
    val type: ChatType,
    val text: String = "",
    val html: String? = null,
    val attachment: ChatAttachment? = null,
)

/**
 * 聊天流适配器：用户气泡 / AI 气泡 / 状态行 / HTML 产物卡片。
 * 支持对指定消息做流式追加（AI 输出逐行增长）。
 */
class ChatAdapter(
    private val onOpenHtml: (String) -> Unit,
) : RecyclerView.Adapter<ChatAdapter.VH>() {

    private val items = mutableListOf<ChatMessage>()

    class VH(val binding: ItemChatMessageBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(ItemChatMessageBinding.inflate(LayoutInflater.from(parent.context), parent, false))

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: VH, position: Int) {
        val m = items[position]
        val b = holder.binding
        b.layoutUser.visibility = View.GONE
        b.layoutAi.visibility = View.GONE
        b.tvStatus.visibility = View.GONE
        b.imgAttach.visibility = View.GONE
        b.tvAttachName.visibility = View.GONE
        b.htmlCard.visibility = View.GONE

        when (m.type) {
            ChatType.USER -> {
                b.layoutUser.visibility = View.VISIBLE
                b.tvUser.text = m.text
                m.attachment?.let { a ->
                    if (a.isImage && a.uri != null) {
                        b.imgAttach.visibility = View.VISIBLE
                        b.imgAttach.setImageURI(a.uri)
                        b.tvAttachName.visibility = View.GONE
                    } else {
                        b.tvAttachName.visibility = View.VISIBLE
                        b.tvAttachName.text = "📎 ${a.name}"
                    }
                }
            }
            ChatType.AI -> {
                b.layoutAi.visibility = View.VISIBLE
                b.tvAi.text = m.text.ifEmpty { "…" }
                b.tvAi.setTextColor(
                    ContextCompat.getColor(holder.itemView.context, R.color.text_primary)
                )
            }
            ChatType.STATUS -> {
                b.tvStatus.visibility = View.VISIBLE
                b.tvStatus.text = m.text
            }
            ChatType.HTML -> {
                b.layoutAi.visibility = View.VISIBLE
                b.tvAi.text = m.text
                b.htmlCard.visibility = View.VISIBLE
                val html = m.html
                b.btnOpenHtml.setOnClickListener {
                    if (html != null) onOpenHtml(html)
                }
            }
        }
    }

    fun add(m: ChatMessage) {
        items.add(m)
        notifyItemInserted(items.size - 1)
    }

    /** 将指定消息替换为新实例（返回替换后的对象，调用方需续传以保持定位有效）。 */
    fun replace(target: ChatMessage, text: String, type: ChatType = target.type, html: String? = target.html): ChatMessage {
        val idx = items.indexOf(target)
        if (idx < 0) return target
        val updated = items[idx].copy(text = text, type = type, html = html)
        items[idx] = updated
        notifyItemChanged(idx)
        return updated
    }
}
