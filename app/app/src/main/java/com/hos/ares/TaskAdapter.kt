package com.hos.ares

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.hos.ares.databinding.ItemTaskCardBinding
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 侧边栏任务卡列表适配器。每张卡对应一个任务（目录）。
 */
class TaskAdapter(
    private var items: List<Task>,
    private val onClick: (Task) -> Unit,
) : RecyclerView.Adapter<TaskAdapter.VH>() {

    private val timeFmt = SimpleDateFormat("MM-dd HH:mm", Locale.getDefault())

    class VH(val binding: ItemTaskCardBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(ItemTaskCardBinding.inflate(LayoutInflater.from(parent.context), parent, false))

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: VH, position: Int) {
        val t = items[position]
        holder.binding.tvTitle.text = t.title
        holder.binding.tvDir.text = t.directory
        holder.binding.tvTime.text = timeFmt.format(Date(t.lastOpened))
        holder.binding.card.setOnClickListener { onClick(t) }
    }

    fun submit(list: List<Task>) {
        items = list
        notifyDataSetChanged()
    }
}
