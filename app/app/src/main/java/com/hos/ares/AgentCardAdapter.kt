package com.hos.ares

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.hos.ares.databinding.ItemAgentCardBinding

/**
 * Agent 状态卡片条适配器。每张卡对应一个 Agent 运行状态，点击查看详情。
 */
class AgentCardAdapter(
    private var items: List<AgentRunEvent>,
    private val onClick: (AgentRunEvent) -> Unit,
) : RecyclerView.Adapter<AgentCardAdapter.VH>() {

    class VH(val binding: ItemAgentCardBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(ItemAgentCardBinding.inflate(LayoutInflater.from(parent.context), parent, false))

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: VH, position: Int) {
        val e = items[position]
        holder.binding.tvAgentName.text = e.skill
        holder.binding.tvAgentStatus.text = statusText(e.status)
        holder.binding.tvAgentStatus.setTextColor(ContextCompat.getColor(holder.itemView.context, statusColor(e.status)))
        holder.binding.card.setOnClickListener { onClick(e) }
    }

    private fun statusText(s: AgentStatus): String = when (s) {
        AgentStatus.PENDING -> "等待中"
        AgentStatus.RUNNING -> "运行中"
        AgentStatus.DONE -> "完成"
        AgentStatus.FAILED -> "失败"
    }

    private fun statusColor(s: AgentStatus): Int = when (s) {
        AgentStatus.PENDING -> R.color.text_muted
        AgentStatus.RUNNING -> R.color.accent
        AgentStatus.DONE -> R.color.accent_green
        AgentStatus.FAILED -> R.color.error
    }

    fun submit(list: List<AgentRunEvent>) {
        items = list
        notifyDataSetChanged()
    }
}
