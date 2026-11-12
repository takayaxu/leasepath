-- Keep the editable manuscript readable while using vector figures and
-- breakable identifiers in the generated LaTeX reading copy.
function Image(el)
  if FORMAT:match('latex') and el.src:match('%.png$') then
    el.src = el.src:gsub('%.png$', '.pdf')
  end
  return el
end

function Code(el)
  if FORMAT:match('latex') then
    return pandoc.RawInline('latex', '\\nolinkurl{' .. el.text .. '}')
  end
  return el
end

function Para(el)
  if FORMAT:match('latex') and
     pandoc.utils.stringify(el):match('^Algorithm 1:') then
    return {pandoc.RawBlock('latex', '\\Needspace{30\\baselineskip}'), el}
  end
  return el
end

function Table(el)
  if FORMAT:match('latex') and #el.colspecs == 6 then
    local widths = {0.24, 0.152, 0.152, 0.152, 0.152, 0.152}
    for i, width in ipairs(widths) do
      el.colspecs[i][2] = width
    end
  end
  return el
end
